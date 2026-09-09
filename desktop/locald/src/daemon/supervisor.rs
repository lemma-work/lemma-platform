use super::*;

impl Daemon {
    pub(super) fn wait_for_supervisor(self: &Arc<Self>, mut request: Value) -> io::Result<()> {
        let sequence = self.next_internal_request.fetch_add(1, Ordering::Relaxed);
        let id = format!("locald-internal-{sequence}");
        request["id"] = Value::String(id.clone());
        let (sender, receiver) = mpsc::channel();
        self.supervisor_waiters
            .lock()
            .expect("supervisor waiter lock poisoned")
            .insert(id.clone(), sender);
        if let Err(error) = self.send_to_supervisor(request) {
            self.supervisor_waiters
                .lock()
                .expect("supervisor waiter lock poisoned")
                .remove(&id);
            return Err(error);
        }

        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20 * 60);
        let result = loop {
            let remaining = deadline.saturating_duration_since(std::time::Instant::now());
            if remaining.is_zero() {
                break Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "private infrastructure operation timed out",
                ));
            }
            match receiver.recv_timeout(remaining) {
                Ok(event) if event.get("event").and_then(Value::as_str) == Some("done") => {
                    if event.get("ok").and_then(Value::as_bool) == Some(true) {
                        break Ok(());
                    }
                    break Err(io::Error::other("private infrastructure operation failed"));
                }
                Ok(event) if event.get("event").and_then(Value::as_str) == Some("error") => {
                    break Err(io::Error::other(
                        event
                            .get("message")
                            .and_then(Value::as_str)
                            .unwrap_or("private infrastructure operation failed"),
                    ));
                }
                Ok(_) => continue,
                Err(error) => break Err(io::Error::other(error.to_string())),
            }
        };
        self.supervisor_waiters
            .lock()
            .expect("supervisor waiter lock poisoned")
            .remove(&id);
        result
    }

    pub(super) fn supervisor_running(&self) -> bool {
        let mut guard = self.supervisor.lock().expect("supervisor lock poisoned");
        let Some(process) = guard.as_mut() else {
            return false;
        };
        match process.child.try_wait() {
            Ok(None) => true,
            Ok(Some(_)) | Err(_) => {
                *guard = None;
                false
            }
        }
    }

    pub(super) fn ensure_supervisor(self: &Arc<Self>) -> io::Result<()> {
        if self.supervisor_running() {
            return Ok(());
        }

        let mut command = supervisor_command()?;
        command
            .env("LEMMA_DESKTOP", "1")
            // Handed straight to the bundled supervisor, which ships in the
            // same release as this daemon, so both ends move together and no
            // compatibility name is needed here.
            .env(
                "LEMMA_CONTAINER_RUNTIME",
                env::var("LEMMA_CONTAINER_RUNTIME").unwrap_or_else(|_| "auto".into()),
            )
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut child = command.spawn()?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| io::Error::other("supervisor stdin was not piped"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| io::Error::other("supervisor stdout was not piped"))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| io::Error::other("supervisor stderr was not piped"))?;

        *self.supervisor.lock().expect("supervisor lock poisoned") =
            Some(SupervisorProcess { child, stdin });

        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                match read_bounded_line(&mut reader) {
                    Ok(Some(line)) => match serde_json::from_str::<Value>(&line) {
                        Ok(event) => daemon.handle_supervisor_event(event),
                        Err(_) => daemon.broadcast(json!({
                            "v": PROTOCOL_VERSION,
                            "event": "log",
                            "source": "supervisor-stdout",
                            "line": line,
                        })),
                    },
                    Ok(None) => break,
                    Err(error) => {
                        daemon.broadcast(error_event(
                            "supervisor-protocol",
                            format!("supervisor output failed: {error}"),
                            None,
                        ));
                        break;
                    }
                }
            }
            daemon.supervisor_exited();
        });

        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let mut reader = BufReader::new(stderr);
            while let Ok(Some(line)) = read_bounded_line(&mut reader) {
                daemon.broadcast(json!({
                    "v": PROTOCOL_VERSION,
                    "event": "log",
                    "source": "supervisor-stderr",
                    "line": line,
                }));
            }
        });

        Ok(())
    }

    pub(super) fn send_to_supervisor(self: &Arc<Self>, request: Value) -> io::Result<()> {
        self.ensure_supervisor()?;
        let mut guard = self.supervisor.lock().expect("supervisor lock poisoned");
        let process = guard
            .as_mut()
            .ok_or_else(|| io::Error::other("supervisor exited during startup"))?;
        writeln!(process.stdin, "{request}")?;
        process.stdin.flush()
    }

    pub(super) fn supervisor_exited(&self) {
        let status = self
            .supervisor
            .lock()
            .expect("supervisor lock poisoned")
            .take()
            .and_then(|mut process| process.child.wait().ok());
        self.broadcast(error_event(
            "supervisor-exited",
            format!("compatibility supervisor exited ({status:?})"),
            None,
        ));
    }

    pub(super) fn handle_supervisor_event(&self, event: Value) {
        let internal_id = event
            .get("id")
            .and_then(Value::as_str)
            .filter(|id| id.starts_with("locald-internal-"));
        if let Some(id) = internal_id {
            if let Some(waiter) = self
                .supervisor_waiters
                .lock()
                .expect("supervisor waiter lock poisoned")
                .get(id)
            {
                let _ = waiter.send(event.clone());
            }
            if matches!(
                event.get("event").and_then(Value::as_str),
                Some("ack" | "done" | "error")
            ) {
                return;
            }
        }
        self.broadcast(event);
    }
}

pub(super) fn supervisor_command() -> io::Result<Command> {
    let mut command = supervisor_base_command()?;
    command.arg("supervise");
    Ok(command)
}

pub(super) fn supervisor_base_command() -> io::Result<Command> {
    if let Some(path) = env::var_os("LEMMA_LOCALD_SUPERVISOR_BIN")
        .or_else(|| env::var_os("LEMMA_DESKTOP_SUPERVISOR_BIN"))
        .map(PathBuf::from)
        .filter(|path| path.exists())
    {
        let mut command = Command::new(path);
        command.no_console_window();
        return Ok(command);
    }

    if let Ok(executable) = env::current_exe() {
        if let Some(parent) = executable.parent() {
            let sibling = parent.join(if cfg!(windows) {
                "lemma-supervisor.exe"
            } else {
                "lemma-supervisor"
            });
            if sibling.exists() {
                let mut command = Command::new(sibling);
                command.no_console_window();
                return Ok(command);
            }
        }
    }

    let root = env::var_os("LEMMA_DESKTOP_RUNTIME_ROOT")
        .map(PathBuf::from)
        .or_else(|| {
            // desktop/locald -> desktop -> the repo checkout. Two levels, not
            // one, since this crate moved under desktop/ with the rest of the
            // native stack.
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .and_then(Path::parent)
                .map(PathBuf::from)
        })
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "runtime root not found"))?;
    if !root.join("lemma-stack/pyproject.toml").exists() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "no bundled compatibility supervisor or lemma-stack checkout found",
        ));
    }

    let mut command = Command::new("uv");
    command.no_console_window().current_dir(root).args([
        "run",
        "--project",
        "lemma-stack",
        "lemma-stack",
    ]);
    Ok(command)
}

pub(super) fn prepare_compatibility_host_manifest(
    paths: &LocalPaths,
    pack_root: &std::path::Path,
) -> io::Result<PathBuf> {
    // Dev/compatibility daemons are often terminated with the Tauri process.
    // Reclaim only the exact prior installation processes recorded in locald's
    // verified ledger before rendering a new manifest, matching the packaged
    // managed-runtime path. Otherwise an orphaned Next server can retain the
    // fixed compatibility port and make every later launch fail at 80–90%.
    crate::host_process::reclaim_persisted_installation_processes(&paths.root)?;
    let destination = paths.root.join("host-pack.json");
    let provider = transitional_provider(false);
    let mut command = supervisor_base_command()?;
    command
        .args(["host-manifest", "--pack-root"])
        .arg(pack_root)
        .arg("--output")
        .arg(&destination)
        .args(["--provider", &provider])
        .env("LEMMA_DESKTOP", "1");
    let output = command.output()?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        return Err(io::Error::other(format!(
            "could not prepare native host pack: {}",
            detail.trim()
        )));
    }
    if !destination.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "host manifest renderer did not create its output",
        ));
    }
    Ok(destination)
}

pub(super) fn transitional_provider(managed_runtime_available: bool) -> String {
    match env::var("LEMMA_CONTAINER_RUNTIME") {
        Ok(provider)
            if matches!(provider.as_str(), "docker" | "podman")
                || (provider == "lemma_local" && managed_runtime_available) =>
        {
            provider
        }
        _ if managed_runtime_available => "lemma_local".into(),
        _ if Command::new("podman")
            .no_console_window()
            .arg("--version")
            .output()
            .is_ok() =>
        {
            "podman".into()
        }
        _ if Command::new("docker")
            .no_console_window()
            .arg("--version")
            .output()
            .is_ok() =>
        {
            "docker".into()
        }
        // The compatibility supervisor can install Podman when neither CLI is
        // present. Render the matching backend profile in advance.
        _ => "podman".into(),
    }
}
