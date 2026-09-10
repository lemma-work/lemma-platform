use super::*;

pub(crate) fn require_no_recovery(shell: &Shell) -> Result<(), String> {
    if shell.recovery_mode.load(Ordering::Acquire) {
        return Err("Recovery mode: local services and downloads are paused. Use Recovery, or choose a connection mode to resume.".into());
    }
    if shell.recovery_running.load(Ordering::Acquire) {
        return Err("Installation cleanup is running. Wait for it to finish before starting local services.".into());
    }
    Ok(())
}

pub(crate) fn ensure_locald(app: &AppHandle) -> Result<(), String> {
    let shell: State<Shell> = app.state();
    require_no_recovery(&shell)?;
    // The runtime comes first, and before the "already connected" check rather
    // than after it.
    //
    // Both modes run this same daemon: hosted brings it up through
    // `ensure_locald_without_host_pack` so the Agent Host has a supervisor, and
    // only local needs the runtime artifacts. So by the time someone switches
    // hosted -> local, the writer is already `Some` -- and this returned `Ok`
    // having installed nothing at all. `start` then reached a daemon with no
    // private runtime and came back "private runtime is not ready for host
    // processes", the splash sat on "Lemma is starting", and the only cure was
    // relaunching the app, because a fresh process is the one thing that makes
    // the writer `None` again and lets this run properly.
    //
    // Cheap when there is nothing to do: an installed runtime is self-contained
    // and is recognised from its own recorded identity, without consulting an
    // artifact host.
    //
    // Still before the connect guard, under a lock of its own. This can take
    // minutes on a first run, and holding `locald_connect` across it turned
    // every unrelated caller into a hang of the same length.
    {
        let _install_guard = shell.runtime_install.lock().unwrap();
        require_no_recovery(&shell)?;
        ensure_runtime_artifacts(app)?;
    }
    if shell.locald_writer.lock().unwrap().is_some() {
        return Ok(());
    }

    let _connect_guard = shell.locald_connect.lock().unwrap();
    require_no_recovery(&shell)?;
    if shell.locald_writer.lock().unwrap().is_some() {
        return Ok(());
    }

    let required_root = host_pack_root();
    let required_release = required_root.as_deref().and_then(host_pack_release);
    let expected_executable = locald_binary();
    // The host pack says which runtime it serves; the executable says which
    // build is serving it. A daemon left over from a replaced app bundle can
    // satisfy the first and still be the wrong process.
    let acceptable = |hello: &Value| {
        locald_is_this_build(hello, expected_executable.as_deref())
            && locald_matches_host_pack(
                hello,
                required_release.as_deref(),
                required_root.as_deref(),
            )
    };
    if let Ok(connection) = connect_locald() {
        if acceptable(&connection.hello) {
            install_locald_connection(app, connection);
            return Ok(());
        }
        replace_locald(connection)?;
    }

    let child = spawn_locald()?;
    let connection = await_locald(child, |connection| acceptable(&connection.hello))?;
    install_locald_connection(app, connection);
    Ok(())
}

/// Bring up locald for a workspace that has no local stack.
///
/// A hosted workspace still wants the Agent Host on this machine, and locald is
/// what supervises it. Unlike the local path this downloads nothing: with no
/// host pack there is no release to match, and locald without one only holds
/// its socket and the sidecar.
///
/// It does still insist the daemon be this build's. It used to accept whatever
/// was listening, and "whatever" turned out to include the previous app
/// bundle's daemon, still running from the Trash after an update, supervising
/// the previous release's runtime. Adopting it made the Agent Host controls
/// report and command stale state, and left a local start booting a runtime the
/// installed app had already replaced.
pub(crate) fn ensure_locald_without_host_pack(app: &AppHandle) -> Result<(), String> {
    let shell: State<Shell> = app.state();
    require_no_recovery(&shell)?;
    if shell.locald_writer.lock().unwrap().is_some() {
        return Ok(());
    }
    let _connect_guard = shell.locald_connect.lock().unwrap();
    require_no_recovery(&shell)?;
    if shell.locald_writer.lock().unwrap().is_some() {
        return Ok(());
    }

    let expected = locald_binary();
    if let Ok(connection) = connect_locald() {
        if locald_is_this_build(&connection.hello, expected.as_deref()) {
            install_locald_connection(app, connection);
            return Ok(());
        }
        replace_locald(connection)?;
    }

    let child = spawn_locald()?;
    let connection = await_locald(child, |connection| {
        locald_is_this_build(&connection.hello, expected.as_deref())
    })?;
    install_locald_connection(app, connection);
    Ok(())
}

pub(crate) fn spawn_locald() -> Result<Child, String> {
    let root = runtime_root();
    let have_checkout = root.join("desktop/locald/Cargo.toml").exists();
    let locald_bin = locald_binary();

    let mut command = match &locald_bin {
        Some(bin) => Command::new(bin),
        None => {
            if !have_checkout {
                return Err(format!(
                    "runtime not found: {} has no locald checkout and no bundled daemon",
                    root.display()
                ));
            }
            let mut fallback = Command::new("cargo");
            fallback.args([
                "run",
                "--quiet",
                "--manifest-path",
                "desktop/locald/Cargo.toml",
                "--",
                "serve",
            ]);
            fallback
        }
    };
    if have_checkout {
        command.current_dir(&root);
    }
    // The daemon inherits this process's environment, and the daemon reads
    // redirect variables of its own. So gating them in the *app* -- which
    // `dev_override` does -- stops at the process boundary: a signed, notarized
    // build would refuse to load a host pack from an environment variable and
    // then hand that same variable to the daemon, which loads it without
    // asking. Same threat model the gating exists for: anything running as the
    // user laundering trust through a hardened-runtime process.
    //
    // Removed rather than cleared. `env_clear` would take PATH, HOME and the
    // locale with it, and the ones set below are set explicitly anyway --
    // removal has to happen first so those still win.
    if !cfg!(debug_assertions) {
        for name in DAEMON_REDIRECT_ENV {
            command.env_remove(name);
        }
    }
    command
        .env("PATH", enriched_path())
        .env("LEMMA_DESKTOP", "1")
        .env("LEMMA_LOCALD_ROOT", locald_root())
        .env("LEMMA_DESKTOP_RUNTIME_ROOT", &root)
        // Which container runtime to use -- docker, podman, lemma_local, or
        // "auto" to detect. Not the sandbox provider: the backend's
        // WORKSPACE_PROVIDER is derived from this separately and takes a
        // narrower set of values.
        .env(
            "LEMMA_CONTAINER_RUNTIME",
            std::env::var("LEMMA_CONTAINER_RUNTIME").unwrap_or_else(|_| "auto".into()),
        )
        .stdin(Stdio::null())
        .stdout(Stdio::null())
        .stderr(locald_stderr_sink());
    if let Some(pack_root) = host_pack_root() {
        command.env("LEMMA_LOCALD_HOST_PACK_ROOT", pack_root);
    }
    if let Some(runtime_root) = managed_runtime_root() {
        let bridge =
            bundled_sibling("lemma-runtime").ok_or("bundled lemma-runtime bridge is missing")?;
        command
            .env("LEMMA_LOCALD_MANAGED_RUNTIME_ARTIFACT_ROOT", runtime_root)
            .env("LEMMA_LOCALD_RUNTIME_BRIDGE_BIN", bridge);
        #[cfg(target_os = "macos")]
        command.env(
            "LEMMA_LOCALD_VZ_BIN",
            bundled_vz().ok_or("bundled lemma-vz helper is missing")?,
        );
    }
    command
        .no_console_window()
        .spawn()
        .map_err(|e| format!("failed to spawn lemma-locald: {e}"))
}

/// Wait for the control endpoint, giving up early if the daemon exited.
pub(crate) fn await_locald<F>(mut child: Child, accept: F) -> Result<LocaldConnection, String>
where
    F: Fn(&LocaldConnection) -> bool,
{
    let deadline = Instant::now() + LOCALD_START_BUDGET;
    let mut last_error = "daemon did not create its control endpoint".to_string();
    while Instant::now() < deadline {
        match connect_locald() {
            Ok(connection) if accept(&connection) => return Ok(connection),
            Ok(_) => last_error = "daemon started with the wrong native host pack".into(),
            Err(error) => last_error = error,
        }
        // A daemon that has already exited will never open the endpoint, so say
        // why instead of spending the rest of the budget waiting for it.
        if let Ok(Some(status)) = child.try_wait() {
            // The status alone is "exit status: 1", which tells nobody
            // anything. The daemon writes the actual reason to its stderr, and
            // since it exited there is nothing left to race with for the read.
            return Err(match locald_stderr_tail() {
                Some(reason) => format!("lemma-locald could not start: {reason}"),
                None => format!("lemma-locald exited during startup ({status})"),
            });
        }
        std::thread::sleep(LOCALD_POLL_INTERVAL);
    }
    Err(format!("could not connect to lemma-locald: {last_error}"))
}

pub(crate) fn connect_locald() -> Result<LocaldConnection, String> {
    connect_locald_with_mode(false)
}

pub(crate) fn connect_locald_with_mode(nonblocking: bool) -> Result<LocaldConnection, String> {
    let root = locald_root();
    let token_path = root.join("control.token");
    if std::fs::metadata(&token_path).is_ok_and(|meta| meta.len() > 4096) {
        return Err("control token exceeds its size limit".into());
    }
    let token = std::fs::read_to_string(token_path)
        .map_err(|error| format!("control token unavailable: {error}"))?;
    let mut stream = LocalSocketStream::connect(locald_socket_name(&root)?)
        .map_err(|error| format!("control endpoint unavailable: {error}"))?;
    // Named pipes do not support synchronous read timeouts. Nonblocking I/O
    // bounds this handshake on both platforms without leaving a waiter thread.
    stream
        .set_nonblocking(true)
        .map_err(|error| error.to_string())?;
    writeln!(
        stream,
        "{}",
        json!({"v": 1, "cmd": "hello", "token": token.trim(), "client": "desktop"})
    )
    .map_err(|error| format!("daemon authentication failed: {error}"))?;
    stream
        .flush()
        .map_err(|error| format!("daemon authentication failed: {error}"))?;
    let line = ipc_read::handshake_line(&mut stream, LOCALD_HANDSHAKE_BUDGET, 1024 * 1024)
        .map_err(|error| format!("daemon handshake failed: {error}"))?;
    let hello: Value = serde_json::from_str(line.trim_end())
        .map_err(|error| format!("invalid daemon handshake: {error}"))?;
    if hello["event"].as_str() != Some("hello") || hello["protocol"].as_u64() != Some(1) {
        return Err("incompatible lemma-locald handshake".into());
    }
    stream
        .set_nonblocking(nonblocking)
        .map_err(|error| error.to_string())?;
    let (receive, send) = stream.split();
    Ok(LocaldConnection {
        reader: BufReader::new(receive),
        writer: send,
        hello,
    })
}

pub(crate) fn request_locald_replacement(connection: &mut LocaldConnection) -> Result<(), String> {
    writeln!(
        connection.writer,
        "{}",
        json!({"v": 1, "cmd": "shutdown-daemon", "id": "desktop-upgrade"})
    )
    .map_err(|error| format!("could not request daemon replacement: {error}"))?;
    connection
        .writer
        .flush()
        .map_err(|error| format!("could not request daemon replacement: {error}"))
}

pub(crate) fn wait_for_locald_exit(attempts: usize, reason: &str) -> Result<(), String> {
    let root = locald_root();
    let name = locald_socket_name(&root)?;
    for _ in 0..attempts {
        if LocalSocketStream::connect(name.clone()).is_err() {
            return Ok(());
        }
        std::thread::sleep(LOCALD_EXIT_POLL);
    }
    Err(format!(
        "the local service manager did not stop for {reason}"
    ))
}

pub(crate) fn replace_locald(connection: LocaldConnection) -> Result<(), String> {
    // An update has all the time it needs: the alternative is a new app beside
    // an old daemon, which is worse than a slow update.
    stop_locald(connection, "the app update", 450)
}

/// Stop the daemon, gracefully if it will and by verified identity if it will
/// not.
///
/// `reason` only names the occasion in the error text. The two occasions are an
/// app update, which replaces the daemon, and quitting, which must not leave
/// one behind -- see [`leave_nothing_running`].
pub(crate) fn stop_locald(
    mut connection: LocaldConnection,
    reason: &str,
    graceful_attempts: usize,
) -> Result<(), String> {
    let original_pid = connection.hello["pid"]
        .as_u64()
        .ok_or("the previous local service manager did not report its process identity")?;
    request_locald_replacement(&mut connection)?;
    drop(connection);
    if wait_for_locald_exit(graceful_attempts, reason).is_ok() {
        return Ok(());
    }

    // Old same-version builds can be trapped inside a runtime recovery
    // operation and reject their own graceful replacement command forever.
    // Re-authenticate immediately before the fallback, require the identical
    // PID and exact packaged executable path, then terminate only that daemon.
    // Its process ledgers let the replacement daemon reclaim app-owned
    // children without ever targeting unrelated host processes.
    let current = connect_locald().map_err(|error| {
        format!(
            "the previous local service manager remained busy and could not be verified: {error}"
        )
    })?;
    if current.hello["pid"].as_u64() != Some(original_pid) {
        return Err(format!(
            "the local service manager changed during {reason}; reopen Lemma to retry"
        ));
    }
    force_terminate_packaged_locald(original_pid)?;
    wait_for_locald_exit(LOCALD_FORCE_EXIT_ATTEMPTS, reason)
}

#[cfg(target_os = "macos")]
pub(crate) fn force_terminate_packaged_locald(pid: u64) -> Result<(), String> {
    let pid =
        i32::try_from(pid).map_err(|_| "the local service manager PID is invalid".to_string())?;
    if pid <= 1 || pid == std::process::id() as i32 {
        return Err("refusing to terminate an invalid local service manager process".into());
    }
    let actual = macos_process_path(pid)?;
    let expected =
        bundled_locald().ok_or("the packaged local service manager executable is missing")?;
    if actual != expected {
        return Err(format!(
            "refusing to stop an unexpected process during update: {}",
            actual.display()
        ));
    }
    stop_packaged_vz_child(pid)?;
    let result = unsafe { libc::kill(pid, libc::SIGTERM) };
    if result != 0 {
        return Err(format!(
            "could not terminate the stale local service manager: {}",
            std::io::Error::last_os_error()
        ));
    }
    Ok(())
}

#[cfg(target_os = "macos")]
pub(crate) fn macos_process_path(pid: i32) -> Result<PathBuf, String> {
    let mut buffer = vec![0_u8; libc::PROC_PIDPATHINFO_MAXSIZE as usize];
    let length = unsafe {
        libc::proc_pidpath(
            pid,
            buffer.as_mut_ptr().cast(),
            libc::PROC_PIDPATHINFO_MAXSIZE as u32,
        )
    };
    if length <= 0 {
        return Err("could not verify the local service manager executable".into());
    }
    let terminator = buffer
        .iter()
        .position(|byte| *byte == 0)
        .unwrap_or(buffer.len());
    Ok(PathBuf::from(
        String::from_utf8_lossy(&buffer[..terminator]).into_owned(),
    ))
}

#[cfg(target_os = "macos")]
pub(crate) fn stop_packaged_vz_child(parent_pid: i32) -> Result<(), String> {
    let output = Command::new("/usr/bin/pgrep")
        .args(["-P", &parent_pid.to_string()])
        .output()
        .map_err(|error| format!("could not inspect the previous runtime helpers: {error}"))?;
    if !output.status.success() {
        return Ok(());
    }
    let expected = bundled_vz()
        .ok_or("the packaged VM helper executable is missing")?
        .canonicalize()
        .map_err(|error| format!("could not verify the packaged VM helper: {error}"))?;
    for child_pid in String::from_utf8_lossy(&output.stdout)
        .lines()
        .filter_map(|value| value.trim().parse::<i32>().ok())
    {
        if child_pid <= 1 {
            continue;
        }
        let Ok(actual) = macos_process_path(child_pid) else {
            continue;
        };
        if actual.canonicalize().ok().as_ref() != Some(&expected) {
            continue;
        }
        // VZ handles SIGTERM as a graceful guest stop. Bound that path, then
        // force only the exact verified helper so an upgrade cannot leave the
        // private data disk attached to an orphan.
        if unsafe { libc::kill(child_pid, libc::SIGTERM) } != 0 {
            let error = std::io::Error::last_os_error();
            if error.raw_os_error() != Some(libc::ESRCH) {
                return Err(format!("could not stop the previous VM helper: {error}"));
            }
            continue;
        }
        let deadline = std::time::Instant::now() + VM_STOP_GRACE_BUDGET;
        while std::time::Instant::now() < deadline {
            if unsafe { libc::kill(child_pid, 0) } != 0 {
                break;
            }
            std::thread::sleep(Duration::from_millis(100));
        }
        if unsafe { libc::kill(child_pid, 0) } == 0 {
            let _ = unsafe { libc::kill(child_pid, libc::SIGKILL) };
            let reap_deadline = std::time::Instant::now() + VM_STOP_REAP_BUDGET;
            while std::time::Instant::now() < reap_deadline {
                if unsafe { libc::kill(child_pid, 0) } != 0 {
                    std::thread::sleep(Duration::from_millis(500));
                    break;
                }
                std::thread::sleep(Duration::from_millis(50));
            }
        }
    }
    Ok(())
}

#[cfg(not(target_os = "macos"))]
pub(crate) fn force_terminate_packaged_locald(_pid: u64) -> Result<(), String> {
    Err("the previous local service manager is still busy; quit Lemma and retry the update".into())
}

pub(crate) fn stop_locald_for_runtime_maintenance(app: &AppHandle) -> Result<(), String> {
    match connect_locald() {
        Ok(connection) => replace_locald(connection)?,
        Err(error) => {
            let root = locald_root();
            if LocalSocketStream::connect(locald_socket_name(&root)?).is_ok() {
                return Err(format!("A local service is still running but cannot be authenticated: {error}. Close that installation or restart this computer, then retry Recovery. No local data has been erased."));
            }
        }
    }
    let shell: State<Shell> = app.state();
    *shell.locald_writer.lock().unwrap() = None;
    Ok(())
}

pub(crate) fn start_after_runtime_maintenance(
    app: &AppHandle,
    request_id: &str,
) -> Result<(), String> {
    ensure_locald(app)?;
    send_local_operation(app, json!({"cmd":"start"}), operation_id(request_id))
}

pub(crate) fn disconnect_locald(app: &AppHandle) {
    // Disconnect only this desktop client. The daemon and desired service state
    // survive a crash, an upgrade, and a closed window -- which is the point of
    // closing to the tray. They do *not* survive a quit any more; see
    // `leave_nothing_running`.
    let _ = send_to_locald(app, json!({"cmd": "disconnect", "id": "shell-exit"}));
    let shell: State<Shell> = app.state();
    *shell.locald_writer.lock().unwrap() = None;
}
