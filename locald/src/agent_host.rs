use std::fs::{File, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

const LOG_LIMIT_BYTES: u64 = 5 * 1024 * 1024;
const RESTART_BACKOFF: Duration = Duration::from_secs(3);

#[derive(Debug)]
struct SupervisorState {
    child: Option<Child>,
    desired_running: bool,
    restart_count: u64,
    started_at: Option<Instant>,
    started_at_ms: Option<u128>,
    next_restart: Instant,
    last_error: Option<String>,
    last_exit_code: Option<i32>,
}

pub struct AgentHostSupervisor {
    executable: Option<PathBuf>,
    data_dir: PathBuf,
    log_path: PathBuf,
    state: Mutex<SupervisorState>,
}

impl AgentHostSupervisor {
    pub fn discover(locald_root: &Path) -> Self {
        let executable = discover_executable();
        let shared_root = locald_root
            .parent()
            .unwrap_or(locald_root)
            .join("agent-host");
        Self {
            executable,
            data_dir: shared_root.clone(),
            log_path: shared_root.join("agent-host.log"),
            state: Mutex::new(SupervisorState {
                child: None,
                desired_running: true,
                restart_count: 0,
                started_at: None,
                started_at_ms: None,
                next_restart: Instant::now(),
                last_error: None,
                last_exit_code: None,
            }),
        }
    }

    pub fn start(&self) -> io::Result<()> {
        let mut state = self.state.lock().expect("Agent Host state lock poisoned");
        state.desired_running = true;
        if child_running(&mut state) {
            return Ok(());
        }
        self.spawn_locked(&mut state)
    }

    pub fn stop(&self) -> io::Result<()> {
        let mut state = self.state.lock().expect("Agent Host state lock poisoned");
        state.desired_running = false;
        if let Some(mut child) = state.child.take() {
            terminate_process_tree(&mut child)?;
            state.last_exit_code = child.try_wait()?.and_then(|status| status.code());
        }
        state.started_at = None;
        Ok(())
    }

    pub fn restart(&self) -> io::Result<()> {
        self.stop()?;
        self.start()
    }

    pub fn reconcile(&self) -> io::Result<()> {
        let mut state = self.state.lock().expect("Agent Host state lock poisoned");
        if child_running(&mut state) || !state.desired_running {
            return Ok(());
        }
        if state.next_restart > Instant::now() {
            return Ok(());
        }
        self.spawn_locked(&mut state)
    }

    pub fn status(&self) -> Value {
        let mut state = self.state.lock().expect("Agent Host state lock poisoned");
        let running = child_running(&mut state);
        json!({
            "available": self.executable.is_some(),
            "running": running,
            "desired_running": state.desired_running,
            "pid": state.child.as_ref().map(Child::id),
            "executable": self.executable,
            "data_dir": self.data_dir,
            "log": self.log_path,
            "restart_count": state.restart_count,
            "started_at_ms": state.started_at_ms,
            "uptime_seconds": state.started_at.map(|started| started.elapsed().as_secs()),
            "last_exit_code": state.last_exit_code,
            "last_error": state.last_error,
        })
    }

    fn spawn_locked(&self, state: &mut SupervisorState) -> io::Result<()> {
        let executable = self.executable.as_ref().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "lemma-agent-host sidecar is not installed",
            )
        })?;
        std::fs::create_dir_all(&self.data_dir)?;
        rotate_log(&self.log_path)?;
        let stdout = append_log(&self.log_path)?;
        let stderr = stdout.try_clone()?;
        let mut command = Command::new(executable);
        command
            .arg("--data-dir")
            .arg(&self.data_dir)
            .arg("serve")
            .stdin(Stdio::null())
            .stdout(Stdio::from(stdout))
            .stderr(Stdio::from(stderr));
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            command.process_group(0);
        }
        match command.spawn() {
            Ok(child) => {
                state.child = Some(child);
                state.restart_count = state.restart_count.saturating_add(1);
                state.started_at = Some(Instant::now());
                state.started_at_ms = Some(now_ms());
                state.next_restart = Instant::now() + RESTART_BACKOFF;
                state.last_error = None;
                state.last_exit_code = None;
                Ok(())
            }
            Err(error) => {
                state.last_error = Some(error.to_string());
                state.next_restart = Instant::now() + RESTART_BACKOFF;
                Err(error)
            }
        }
    }
}

fn child_running(state: &mut SupervisorState) -> bool {
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

fn discover_executable() -> Option<PathBuf> {
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
    let development = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../agent-host/target/debug")
        .join(filename);
    development.is_file().then_some(development)
}

fn append_log(path: &Path) -> io::Result<File> {
    OpenOptions::new().create(true).append(true).open(path)
}

fn rotate_log(path: &Path) -> io::Result<()> {
    if path.metadata().map(|metadata| metadata.len()).unwrap_or(0) < LOG_LIMIT_BYTES {
        return Ok(());
    }
    let previous = path.with_extension("log.previous");
    let _ = std::fs::remove_file(&previous);
    std::fs::rename(path, previous)
}

#[cfg(unix)]
fn terminate_process_tree(child: &mut Child) -> io::Result<()> {
    let process_group = i32::try_from(child.id())
        .map_err(|_| io::Error::other("Agent Host PID does not fit i32"))?;
    // The child is launched as its own process group. Signalling the negative
    // PID shuts down the host and every ACP adapter it owns.
    unsafe {
        libc::kill(-process_group, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if child.try_wait()?.is_some() {
            return Ok(());
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    unsafe {
        libc::kill(-process_group, libc::SIGKILL);
    }
    child.wait()?;
    Ok(())
}

#[cfg(windows)]
fn terminate_process_tree(child: &mut Child) -> io::Result<()> {
    let status = Command::new("taskkill")
        .args(["/PID", &child.id().to_string(), "/T", "/F"])
        .status()?;
    if !status.success() {
        child.kill()?;
    }
    child.wait()?;
    Ok(())
}

fn now_ms() -> u128 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default()
        .as_millis()
}

#[cfg(test)]
mod tests {
    use super::*;
    use tempfile::tempdir;

    #[test]
    fn status_exposes_sidecar_lifecycle_paths() {
        let root = tempdir().unwrap();
        let supervisor = AgentHostSupervisor::discover(root.path());
        let status = supervisor.status();
        assert_eq!(
            status["data_dir"],
            root.path()
                .parent()
                .unwrap()
                .join("agent-host")
                .to_string_lossy()
                .as_ref()
        );
        assert_eq!(status["running"], false);
    }
}
