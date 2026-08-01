use std::fs::{File, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

const LOG_LIMIT_BYTES: u64 = 5 * 1024 * 1024;
const RESTART_BACKOFF: Duration = Duration::from_secs(3);
/// How long a merged status may be reused. A UI polls this while its page is
/// open, and every miss forks the sidecar to read its own SQLite journal.
const DETAILS_CACHE: Duration = Duration::from_secs(2);
/// `connect` and `disconnect` reach the backend; `refresh` only bumps a
/// generation counter locally but still opens the journal.
const CLI_TIMEOUT: Duration = Duration::from_secs(45);
/// `connect` is not a request, it is an installation.
///
/// Before it can report success it fetches and verifies the pinned adapter
/// package for every certified agent — an npm download each, on a cache that is
/// empty the first time anyone pairs. Judging that by the same deadline as
/// `status` meant the very first pairing on a machine reported "Agent Host did
/// not answer `connect` in time" while the install was still running normally.
const CONNECT_TIMEOUT: Duration = Duration::from_secs(600);
/// `refresh` re-probes every installed agent, and a probe spawns the agent and
/// opens an ACP session with its own 20s ceiling.
const REFRESH_TIMEOUT: Duration = Duration::from_secs(180);

fn cli_timeout(verb: &str) -> Duration {
    match verb {
        "connect" => CONNECT_TIMEOUT,
        "refresh" => REFRESH_TIMEOUT,
        _ => CLI_TIMEOUT,
    }
}

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
    preference_path: PathBuf,
    state: Mutex<SupervisorState>,
    details: Mutex<Option<(Instant, Value)>>,
}

impl AgentHostSupervisor {
    pub fn discover(locald_root: &Path) -> Self {
        let executable = discover_executable();
        let shared_root = locald_root
            .parent()
            .unwrap_or(locald_root)
            .join("agent-host");
        let preference_path = shared_root.join("supervisor.json");
        // Absent preference means the user has never chosen. Run for a paired
        // machine, since it has work waiting; stay off for an unpaired one,
        // where the sidecar would only idle.
        let desired_running = read_preference(&preference_path)
            .unwrap_or_else(|| host_is_paired(&shared_root.join("config.json")));
        Self {
            executable,
            data_dir: shared_root.clone(),
            log_path: shared_root.join("agent-host.log"),
            preference_path,
            state: Mutex::new(SupervisorState {
                child: None,
                desired_running,
                restart_count: 0,
                started_at: None,
                started_at_ms: None,
                next_restart: Instant::now(),
                last_error: None,
                last_exit_code: None,
            }),
            details: Mutex::new(None),
        }
    }

    /// Whether the user wants the Agent Host running, across daemon restarts.
    pub fn desired_running(&self) -> bool {
        self.state
            .lock()
            .expect("Agent Host state lock poisoned")
            .desired_running
    }

    pub fn start(&self) -> io::Result<()> {
        self.write_preference(true);
        let mut state = self.state.lock().expect("Agent Host state lock poisoned");
        state.desired_running = true;
        if child_running(&mut state) {
            return Ok(());
        }
        self.spawn_locked(&mut state)
    }

    pub fn stop(&self) -> io::Result<()> {
        self.write_preference(false);
        self.halt(false)
    }

    /// Stop the process without touching the user's preference.
    ///
    /// The Agent Host runs while the app is open, so quitting has to stop it -
    /// but quitting is not the user turning it off, and it must come back on
    /// the next launch.
    pub fn suspend(&self) -> io::Result<()> {
        self.halt(true)
    }

    fn halt(&self, keep_preference: bool) -> io::Result<()> {
        let mut state = self.state.lock().expect("Agent Host state lock poisoned");
        if !keep_preference {
            state.desired_running = false;
        }
        if let Some(mut child) = state.child.take() {
            state.last_exit_code = terminate_process_tree(&mut child)?;
        }
        state.started_at = None;
        state.started_at_ms = None;
        self.invalidate_details();
        Ok(())
    }

    pub fn restart(&self) -> io::Result<()> {
        self.stop()?;
        self.start()
    }

    pub fn reconcile(&self) -> io::Result<()> {
        // A missing sidecar cannot become available without restarting locald,
        // and `status()` already reports it, so retrying every tick would only
        // grow the daemon log without ever recovering.
        if self.executable.is_none() {
            return Ok(());
        }
        // A healthy Agent Host is spawned once and never again, so rotating
        // only at spawn means it never rotates at all. This tick is the only
        // thing that bounds the log of a host that simply keeps running.
        let rotation = rotate_log(&self.log_path);
        let mut state = self.state.lock().expect("Agent Host state lock poisoned");
        if let Err(error) = rotation {
            state.last_error = Some(format!("could not rotate the Agent Host log: {error}"));
        }
        if child_running(&mut state) || !state.desired_running {
            return Ok(());
        }
        if state.next_restart > Instant::now() {
            return Ok(());
        }
        self.spawn_locked(&mut state)
    }

    /// Whether the process is alive, and nothing about what it is doing.
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

    /// Process state plus what the host itself knows: which workspaces it is
    /// paired to, whether it is actually reaching them, and what work it holds.
    ///
    /// "Running" alone is a poor answer to "is this working?" - a paired host
    /// with an expired secret and an unpaired host that has nothing to do are
    /// both live processes. Only the host's own journal can tell them apart,
    /// and it answers over its CLI rather than over locald's socket.
    pub fn detailed_status(&self) -> Value {
        let mut status = self.status();
        let targets = self.cached_targets();
        let paired = targets.as_ref().is_some_and(|items| !items.is_empty());
        status["paired"] = json!(paired);
        status["targets"] = json!(targets.unwrap_or_default());
        status
    }

    fn cached_targets(&self) -> Option<Vec<Value>> {
        let mut cache = self
            .details
            .lock()
            .expect("Agent Host details lock poisoned");
        if let Some((read_at, value)) = cache.as_ref() {
            if read_at.elapsed() < DETAILS_CACHE {
                return value.as_array().cloned();
            }
        }
        let report = self.run_cli(&["status", "--json"]).ok()?;
        let parsed: Value = serde_json::from_str(&report).ok()?;
        let targets: Vec<Value> = parsed
            .get("targets")
            .and_then(Value::as_array)
            .map(|items| items.iter().map(summarize_target).collect())
            .unwrap_or_default();
        let value = Value::Array(targets.clone());
        *cache = Some((Instant::now(), value));
        Some(targets)
    }

    fn invalidate_details(&self) {
        *self
            .details
            .lock()
            .expect("Agent Host details lock poisoned") = None;
    }

    /// Consume a one-time pairing code, then start serving that workspace.
    ///
    /// The desktop app mints the code with the user's own session and hands it
    /// straight here, so pairing never requires a terminal.
    pub fn pair(&self, url: &str, pairing_code: &str, name: &str) -> io::Result<()> {
        if url.trim().is_empty() || pairing_code.trim().is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "pairing needs a workspace URL and a pairing code",
            ));
        }
        let mut arguments = vec![
            "connect",
            "--url",
            url.trim(),
            "--pairing-code",
            pairing_code.trim(),
        ];
        let name = name.trim();
        if !name.is_empty() {
            arguments.extend_from_slice(&["--name", name]);
        }
        // Plain HTTP is refused off loopback by the host itself, so this only
        // widens what a local development backend already allows.
        if is_loopback_http(url.trim()) {
            arguments.push("--allow-insecure-http");
        }
        self.run_cli(&arguments)?;
        self.invalidate_details();
        self.start()
    }

    /// Revoke this computer's identity remotely, then stop serving.
    pub fn unpair(&self, target_id: Option<&str>) -> io::Result<()> {
        let mut arguments = vec!["disconnect"];
        if let Some(target) = target_id.map(str::trim).filter(|value| !value.is_empty()) {
            arguments.extend_from_slice(&["--target", target]);
        }
        let result = self.run_cli(&arguments);
        self.invalidate_details();
        result?;
        self.stop()
    }

    /// Re-probe the installed coding agents and republish them now, rather than
    /// on the host's own 15-minute cycle.
    pub fn refresh(&self) -> io::Result<()> {
        self.run_cli(&["refresh"])?;
        self.invalidate_details();
        Ok(())
    }

    fn run_cli(&self, arguments: &[&str]) -> io::Result<String> {
        let executable = self.executable.as_ref().ok_or_else(|| {
            io::Error::new(
                io::ErrorKind::NotFound,
                "lemma-agent-host sidecar is not installed",
            )
        })?;
        std::fs::create_dir_all(&self.data_dir)?;
        let mut child = Command::new(executable)
            .arg("--data-dir")
            .arg(&self.data_dir)
            .args(arguments)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .spawn()?;

        let deadline = Instant::now() + cli_timeout(arguments[0]);
        loop {
            if child.try_wait()?.is_some() {
                break;
            }
            if Instant::now() >= deadline {
                let _ = child.kill();
                let _ = child.wait();
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    format!("Agent Host did not answer `{}` in time", arguments[0]),
                ));
            }
            std::thread::sleep(Duration::from_millis(50));
        }

        let output = child.wait_with_output()?;
        if !output.status.success() {
            let detail = String::from_utf8_lossy(&output.stderr);
            let detail = detail.trim();
            return Err(io::Error::other(if detail.is_empty() {
                format!("Agent Host `{}` failed", arguments[0])
            } else {
                // stderr can quote the argument list, and one of those
                // arguments may be a live pairing code.
                redact_secrets(detail, arguments)
            }));
        }
        Ok(String::from_utf8_lossy(&output.stdout).into_owned())
    }

    fn write_preference(&self, enabled: bool) {
        // Shared with the CLI, which supervises the same host when the desktop
        // app is not running, so both agree on what the user last chose.
        if std::fs::create_dir_all(&self.data_dir).is_ok() {
            let _ = std::fs::write(
                &self.preference_path,
                serde_json::to_vec_pretty(&json!({"enabled": enabled})).unwrap_or_default(),
            );
        }
    }

    fn spawn_locked(&self, state: &mut SupervisorState) -> io::Result<()> {
        // Every failure arms the backoff, not just a failed `spawn`: an
        // unwritable data directory would otherwise be retried every tick.
        match self.spawn_process() {
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

    fn spawn_process(&self) -> io::Result<Child> {
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
            // Own process group so `stop` can signal every ACP adapter the host
            // spawned, not just the host itself.
            command.process_group(0);
        }
        command.spawn()
    }
}

fn read_preference(path: &Path) -> Option<bool> {
    let raw = std::fs::read_to_string(path).ok()?;
    serde_json::from_str::<Value>(&raw)
        .ok()?
        .get("enabled")?
        .as_bool()
}

/// Whether the host holds an identity for at least one workspace.
fn host_is_paired(config_path: &Path) -> bool {
    let Ok(raw) = std::fs::read_to_string(config_path) else {
        return false;
    };
    let Ok(config) = serde_json::from_str::<Value>(&raw) else {
        return false;
    };
    config
        .get("targets")
        .and_then(Value::as_array)
        .is_some_and(|targets| {
            targets.iter().any(|target| {
                target
                    .get("enabled")
                    .and_then(Value::as_bool)
                    .unwrap_or(true)
            })
        })
}

/// Reduce one journal entry to what a status view needs.
///
/// The host's own report also carries its service-manager wiring and local
/// paths, which say nothing about whether this workspace is reachable.
fn summarize_target(target: &Value) -> Value {
    let journal = target.get("journal");
    let field = |key: &str| journal.and_then(|value| value.get(key)).cloned();
    json!({
        "target_id": target.get("target_id"),
        "host_id": target.get("host_id"),
        "name": target.get("name"),
        "url": target.get("url"),
        "enabled": target.get("enabled"),
        "connection_state": field("connection_state"),
        "last_connected_at": field("last_connected_at"),
        "last_error": field("last_error"),
        "active_runs": field("active_runs"),
        "pending_events": field("pending_events"),
    })
}

/// Loopback HTTP is the one plain-HTTP case the host accepts, and only when
/// asked. A development backend is served that way.
fn is_loopback_http(url: &str) -> bool {
    let Some(rest) = url.strip_prefix("http://") else {
        return false;
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
    let host = match authority.rsplit_once(':') {
        Some((host, port)) if !port.is_empty() && port.chars().all(|c| c.is_ascii_digit()) => host,
        _ => authority,
    };
    matches!(host, "localhost" | "127.0.0.1" | "[::1]")
}

/// Strip anything from a subprocess message that we passed in as a secret.
fn redact_secrets(detail: &str, arguments: &[&str]) -> String {
    let mut redacted = detail.to_string();
    let mut arguments = arguments.iter().peekable();
    while let Some(argument) = arguments.next() {
        if *argument != "--pairing-code" {
            continue;
        }
        if let Some(secret) = arguments.peek() {
            if !secret.is_empty() {
                redacted = redacted.replace(*secret, "<pairing code>");
            }
        }
    }
    redacted
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

/// Roll an oversized log aside, keeping the file a running host holds open.
///
/// The host inherits its stdout as an append-mode descriptor, and a descriptor
/// follows the inode rather than the path: renaming the file would quietly
/// redirect every subsequent line into the rotated copy and leave the live log
/// empty forever. Copying the contents aside and truncating in place keeps that
/// descriptor pointed at the live file, and append mode resolves the write
/// offset against the current end, so the next line lands at zero.
fn rotate_log(path: &Path) -> io::Result<()> {
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
fn terminate_process_tree(child: &mut Child) -> io::Result<Option<i32>> {
    let process_group = i32::try_from(child.id())
        .map_err(|_| io::Error::other("Agent Host PID does not fit i32"))?;
    // The child is launched as its own process group. Signalling the negative
    // PID shuts down the host and every ACP adapter it owns.
    unsafe {
        libc::kill(-process_group, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if let Some(status) = child.try_wait()? {
            return Ok(status.code());
        }
        std::thread::sleep(Duration::from_millis(50));
    }
    unsafe {
        libc::kill(-process_group, libc::SIGKILL);
    }
    Ok(child.wait()?.code())
}

#[cfg(windows)]
fn terminate_process_tree(child: &mut Child) -> io::Result<Option<i32>> {
    let status = Command::new("taskkill")
        .args(["/PID", &child.id().to_string(), "/T", "/F"])
        .status()?;
    if !status.success() {
        child.kill()?;
    }
    Ok(child.wait()?.code())
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
        let home = tempdir().unwrap();
        // The data directory has to be the sibling of the locald root, because
        // that is where a CLI-paired host keeps its config and journal.
        let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
        let status = supervisor.status();
        assert_eq!(
            status["data_dir"],
            home.path().join("agent-host").to_string_lossy().as_ref()
        );
        assert_eq!(
            status["log"],
            home.path()
                .join("agent-host/agent-host.log")
                .to_string_lossy()
                .as_ref()
        );
        assert_eq!(status["running"], false);
    }

    #[test]
    fn reconcile_is_inert_without_a_sidecar() {
        let home = tempdir().unwrap();
        let mut supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
        supervisor.executable = None;

        supervisor.reconcile().expect("reconcile stays quiet");

        assert_eq!(supervisor.status()["available"], false);
        assert!(!home.path().join("agent-host").exists());
    }

    #[test]
    fn rotation_keeps_the_running_hosts_own_log_descriptor_live() {
        use std::io::Write;

        let home = tempdir().unwrap();
        let log = home.path().join("agent-host.log");
        // The descriptor the host inherited as its stdout.
        let mut inherited = append_log(&log).unwrap();
        inherited
            .write_all(&vec![b'x'; LOG_LIMIT_BYTES as usize])
            .unwrap();
        inherited.flush().unwrap();

        rotate_log(&log).unwrap();
        inherited.write_all(b"after rotation").unwrap();
        inherited.flush().unwrap();

        assert_eq!(
            std::fs::read(&log).unwrap(),
            b"after rotation",
            "the host's inherited descriptor must keep writing to the live log"
        );
        assert_eq!(
            std::fs::metadata(home.path().join("agent-host.log.previous"))
                .unwrap()
                .len(),
            LOG_LIMIT_BYTES,
            "the rotated copy keeps what was there before"
        );
    }

    #[test]
    fn a_long_lived_host_gets_its_log_rotated_without_respawning() {
        let home = tempdir().unwrap();
        let mut supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
        // Pinned so the test does not depend on a sidecar being installed.
        supervisor.executable = Some(home.path().join("lemma-agent-host"));
        std::fs::create_dir_all(&supervisor.data_dir).unwrap();
        std::fs::write(&supervisor.log_path, vec![b'x'; LOG_LIMIT_BYTES as usize]).unwrap();
        // Nothing to spawn: this is the tick a healthy, already-running host
        // takes, which used to leave the log untouched forever.
        supervisor.state.lock().unwrap().desired_running = false;

        supervisor.reconcile().unwrap();

        assert_eq!(std::fs::metadata(&supervisor.log_path).unwrap().len(), 0);
        assert!(supervisor.log_path.with_extension("log.previous").is_file());
    }

    #[test]
    fn a_failed_spawn_arms_the_restart_backoff() {
        let home = tempdir().unwrap();
        let mut supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
        supervisor.executable = Some(home.path().join("missing-agent-host"));

        supervisor
            .start()
            .expect_err("the executable does not exist");

        let state = supervisor.state.lock().unwrap();
        assert!(state.next_restart > Instant::now());
        assert!(state.last_error.is_some());
    }

    fn write(path: &Path, contents: &str) {
        std::fs::create_dir_all(path.parent().unwrap()).unwrap();
        std::fs::write(path, contents).unwrap();
    }

    #[test]
    fn an_unpaired_machine_stays_off_and_a_paired_one_runs() {
        // Nothing has been chosen yet, so the default has to come from whether
        // the host has work: an unpaired sidecar would only idle.
        let home = tempdir().unwrap();
        let locald_root = home.path().join("locald");
        assert!(!AgentHostSupervisor::discover(&locald_root).desired_running());

        write(
            &home.path().join("agent-host/config.json"),
            r#"{"targets": [{"name": "work", "enabled": true}]}"#,
        );
        assert!(AgentHostSupervisor::discover(&locald_root).desired_running());
    }

    #[test]
    fn turning_it_off_survives_a_daemon_restart() {
        let home = tempdir().unwrap();
        let locald_root = home.path().join("locald");
        write(
            &home.path().join("agent-host/config.json"),
            r#"{"targets": [{"name": "work", "enabled": true}]}"#,
        );

        let supervisor = AgentHostSupervisor::discover(&locald_root);
        supervisor.stop().unwrap();
        assert!(!supervisor.desired_running());
        // A paired host would otherwise default back on and undo the choice.
        assert!(!AgentHostSupervisor::discover(&locald_root).desired_running());

        supervisor.start().ok();
        assert!(AgentHostSupervisor::discover(&locald_root).desired_running());
    }

    #[test]
    fn quitting_the_app_does_not_count_as_turning_it_off() {
        let home = tempdir().unwrap();
        let locald_root = home.path().join("locald");
        write(
            &home.path().join("agent-host/config.json"),
            r#"{"targets": [{"name": "work", "enabled": true}]}"#,
        );

        let supervisor = AgentHostSupervisor::discover(&locald_root);
        supervisor.suspend().unwrap();
        assert!(AgentHostSupervisor::discover(&locald_root).desired_running());
    }

    #[test]
    fn detailed_status_reports_reachability_not_just_liveness() {
        // A live process says nothing about whether it can take work: a paired
        // host with a dead connection and an unpaired one both look "running".
        let target = json!({
            "target_id": "target-1",
            "host_id": "host-1",
            "name": "Work",
            "url": "https://api.lemma.work",
            "enabled": true,
            "journal": {
                "connection_state": "OFFLINE",
                "last_error": "connection refused",
                "active_runs": 2,
                "pending_events": 7,
                "last_connected_at": "2026-07-31T12:00:00Z"
            }
        });

        let summary = summarize_target(&target);
        assert_eq!(summary["host_id"], "host-1");
        assert_eq!(summary["connection_state"], "OFFLINE");
        assert_eq!(summary["last_error"], "connection refused");
        assert_eq!(summary["active_runs"], 2);
        assert_eq!(summary["pending_events"], 7);
    }

    #[test]
    fn plain_http_is_opted_into_only_on_loopback() {
        assert!(is_loopback_http("http://localhost:8710"));
        assert!(is_loopback_http("http://127.0.0.1:8710/api"));
        assert!(is_loopback_http("http://[::1]:8710"));
        assert!(is_loopback_http("http://localhost"));
        // A LAN or public address over plain HTTP stays refused.
        assert!(!is_loopback_http("http://192.168.1.10:8710"));
        assert!(!is_loopback_http("http://localhost.evil.example:8710"));
        assert!(!is_loopback_http("https://api.lemma.work"));
    }

    #[test]
    fn a_failure_never_echoes_the_pairing_code() {
        // The host quotes its argument list back on error, and one of those
        // arguments is a live, single-use credential.
        let arguments = [
            "connect",
            "--url",
            "https://api.lemma.work",
            "--pairing-code",
            "s3cret-code",
        ];
        let detail = redact_secrets(
            "connect --pairing-code s3cret-code failed: already paired",
            &arguments,
        );
        assert!(!detail.contains("s3cret-code"));
        assert!(detail.contains("<pairing code>"));
        assert!(detail.contains("already paired"));
    }
}
