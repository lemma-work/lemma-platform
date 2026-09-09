//! The sidecar process: starting it, ending it, and its log.

use super::*;

/// A child that is terminated and reaped however its scope ends.
///
/// `std::process::Child::drop` does neither, so any `?` between a spawn and a
/// `wait` leaks the process -- and `run_cli` had two, on a call that spawns
/// every installed agent.
pub(crate) struct Reaped(pub(crate) Option<Child>);

impl Reaped {
    pub(crate) fn get(&mut self) -> &mut Child {
        self.0
            .as_mut()
            .expect("the child is taken only once, at the end")
    }

    /// Hand the child on to something that consumes it, so `Drop` stands down.
    pub(crate) fn take(&mut self) -> Child {
        self.0.take().expect("the child is taken only once")
    }
}

impl Drop for Reaped {
    fn drop(&mut self) {
        if let Some(mut child) = self.0.take() {
            // The group, so a probe the CLI spawned goes too.
            let _ = terminate_process_tree(&mut child);
        }
    }
}

pub(crate) fn child_running(state: &mut SupervisorState) -> bool {
    let Some(child) = state.child.as_mut() else {
        return false;
    };
    match child.try_wait() {
        Ok(None) => true,
        Ok(Some(status)) => {
            state.last_exit_code = status.code();
            state.last_error = Some(format!("Agent Host exited with {status}"));
            state.child = None;
            state.started_at = None;
            state.next_restart = Instant::now() + RESTART_BACKOFF;
            false
        }
        Err(error) => {
            state.last_error = Some(error.to_string());
            false
        }
    }
}

pub(crate) fn discover_executable() -> Option<PathBuf> {
    if let Some(path) = std::env::var_os("LEMMA_AGENT_HOST_BIN").filter(|value| !value.is_empty()) {
        let path = PathBuf::from(path);
        if path.is_file() {
            return Some(path);
        }
    }
    if let Ok(current) = std::env::current_exe() {
        if let Some(directory) = current.parent() {
            let filename = if cfg!(windows) {
                "lemma-agent-host.exe"
            } else {
                "lemma-agent-host"
            };
            let sibling = directory.join(filename);
            if sibling.is_file() {
                return Some(sibling);
            }
        }
    }
    let filename = if cfg!(windows) {
        "lemma-agent-host.exe"
    } else {
        "lemma-agent-host"
    };
    // One workspace, one target directory: the agent host builds into
    // desktop/target alongside this crate, not into a sibling crate's own.
    let development = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../target/debug")
        .join(filename);
    development.is_file().then_some(development)
}

pub(crate) fn append_log(path: &Path) -> io::Result<File> {
    OpenOptions::new().create(true).append(true).open(path)
}

/// Roll an oversized log aside, keeping the file a running host holds open.
///
/// The host inherits its stdout as an append-mode descriptor, and a descriptor
/// follows the inode rather than the path: renaming the file would quietly
/// redirect every subsequent line into the rotated copy and leave the live log
/// empty forever. Copying the contents aside and truncating in place keeps that
/// descriptor pointed at the live file, and append mode resolves the write
/// offset against the current end, so the next line lands at zero.
pub(crate) fn rotate_log(path: &Path) -> io::Result<()> {
    if path.metadata().map(|metadata| metadata.len()).unwrap_or(0) < LOG_LIMIT_BYTES {
        return Ok(());
    }
    std::fs::copy(path, path.with_extension("log.previous"))?;
    OpenOptions::new().write(true).open(path)?.set_len(0)
}

/// Stop the host and every adapter it owns, reporting how it exited.
///
/// The caller cannot ask afterwards: terminating reaps the child, so a later
/// `try_wait` reports nothing and a status UI would show a blank exit code for
/// every user-initiated stop.
#[cfg(unix)]
pub(crate) fn terminate_process_tree(child: &mut Child) -> io::Result<Option<i32>> {
    let process_group = i32::try_from(child.id())
        .map_err(|_| io::Error::other("Agent Host PID does not fit i32"))?;
    // The child is launched as its own process group. Signalling the negative
    // PID shuts down the host and every ACP adapter it owns.
    unsafe {
        libc::kill(-process_group, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    let mut code = None;
    let mut reaped = false;
    while Instant::now() < deadline {
        if let Some(status) = child.try_wait()? {
            code = status.code();
            reaped = true;
            break;
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    if !reaped {
        unsafe {
            libc::kill(-process_group, libc::SIGKILL);
        }
        code = child.wait()?.code();
    }
    // Reaping the leader is not the group being empty, and this function
    // promises the group. An adapter the host spawned sits in the same group
    // and can outlive its leader by as long as it takes to die -- or for ever,
    // if it ignores `SIGTERM`. Returning at the leader is what let
    // `dropping_a_supervisor_kills_the_process_it_started` see a live group
    // after the drop had returned: `/bin/sh` exits the moment it has forked,
    // and whether the sibling had finished dying by then was a race the test
    // lost about one run in fifty.
    // One budget for the whole teardown, not one per phase: a slow leader must
    // not buy the group a second five seconds. Whatever is left after the
    // leader is what the group gets before the SIGKILL.
    if !wait_for_group_exit(process_group, deadline) {
        unsafe {
            libc::kill(-process_group, libc::SIGKILL);
        }
        // Best effort past this point: a group that survives its own SIGKILL is
        // not something a caller can act on, and the leader is already reaped,
        // so there is nothing left to report but the exit code.
        wait_for_group_exit(process_group, Instant::now() + Duration::from_secs(1));
    }
    Ok(code)
}

/// Poll until no process is left in `process_group`, or `deadline` passes.
///
/// `ESRCH` is the group having drained. `EPERM` counts as drained too: the
/// leader has been reaped by the time this runs, so its pid is free, and a pid
/// recycled into a process owned by somebody else answers with "operation not
/// permitted" rather than "no such process". Reading that as "still ours and
/// still alive" would spin for the whole budget and then signal a stranger.
#[cfg(unix)]
pub(crate) fn wait_for_group_exit(process_group: i32, deadline: Instant) -> bool {
    loop {
        if unsafe { libc::kill(-process_group, 0) } != 0 {
            let errno = io::Error::last_os_error().raw_os_error();
            if errno == Some(libc::ESRCH) || errno == Some(libc::EPERM) {
                return true;
            }
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

#[cfg(windows)]
pub(crate) fn terminate_process_tree(child: &mut Child) -> io::Result<Option<i32>> {
    let status = Command::new("taskkill")
        .no_console_window()
        .args(["/PID", &child.id().to_string(), "/T", "/F"])
        .status()?;
    if !status.success() {
        child.kill()?;
    }
    Ok(child.wait()?.code())
}

/// Never outlive the process this supervisor started.
///
/// Every ordinary path calls `stop()` or `suspend()`, so this fires almost
/// never in production -- and the one place it fired constantly was the test
/// suite, where a supervisor going out of scope left a `lemma-agent-host serve`
/// running forever. One `make desktop-test` leaked two of them, they inherited
/// no terminal and no parent that would ever reap them, and the only sign was a
/// laptop that would not go idle.
///
/// `discover_executable` is why: its last fallback is
/// `CARGO_MANIFEST_DIR/../target/debug/lemma-agent-host`, which exists on any
/// machine that has built the workspace. A test written on the assumption that
/// "no sidecar exists in a test tree" spawned a real one instead.
///
/// A backstop, not a policy. It cannot report an error and does not try; the
/// paths that care about the exit code take it through `halt`.
impl Drop for AgentHostSupervisor {
    fn drop(&mut self) {
        let Ok(mut state) = self.state.lock() else {
            return;
        };
        if let Some(mut child) = state.child.take() {
            let _ = terminate_process_tree(&mut child);
        }
        self.forget_running();
    }
}

pub(crate) fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}
