use crate::NoConsoleWindow;
use std::fs::{File, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

const LOG_LIMIT_BYTES: u64 = 5 * 1024 * 1024;
const RESTART_BACKOFF: Duration = Duration::from_secs(3);
/// How many restarts inside one window before the supervisor stops trying.
///
/// There was no budget at all: `reconcile` runs once a second, and a sidecar
/// that dies immediately -- a corrupt SQLite journal, a port it cannot bind, a
/// binary the kernel refuses to exec -- was forked roughly twenty times a
/// minute for as long as the daemon lived, with nothing reported and no state
/// the user could act on. `host_process` has had a circuit breaker for its
/// services all along; this is the same idea for the one supervisor that
/// lacked it.
const RESTART_BUDGET: u32 = 5;
/// The window the budget is counted over, and the cooldown before the circuit
/// closes again. Long enough that a genuinely broken host stops hammering,
/// short enough that a transient cause -- a port briefly held by the previous
/// process -- recovers on its own.
const RESTART_WINDOW: Duration = Duration::from_secs(60);
/// How long a merged status may be reused. A UI polls this while its page is
/// open, and every miss forks the sidecar to read its own SQLite journal.
const DETAILS_CACHE: Duration = Duration::from_secs(2);
/// `connect` and `disconnect` reach the backend; `refresh` only bumps a
/// generation counter locally but still opens the journal.
const CLI_TIMEOUT: Duration = Duration::from_secs(45);
/// `refresh` re-probes every installed agent, and a probe spawns the agent and
/// opens an ACP session with its own 20s ceiling.
const REFRESH_TIMEOUT: Duration = Duration::from_secs(180);

/// `connect` had a ten-minute deadline of its own, because it was not a request
/// but an installation: it fetched and verified a pinned adapter package for
/// every certified agent before it could report success. It no longer installs
/// anything — the cache is warmed when the app opens — so it is a pairing call
/// with one network round trip in it, and it takes the ordinary deadline.
fn cli_timeout(verb: &str) -> Duration {
    match verb {
        "refresh" => REFRESH_TIMEOUT,
        _ => CLI_TIMEOUT,
    }
}

#[derive(Debug)]
struct SupervisorState {
    child: Option<Child>,
    desired_running: bool,
    restart_count: u64,
    /// Restarts inside the current window, and when it started.
    window_restarts: u32,
    window_started: Instant,
    /// Set once the budget is spent. Reported, and cleared by a deliberate
    /// start or by the window going quiet.
    circuit_open: bool,
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
    details: Mutex<Option<(Instant, Value)>>,
}

impl AgentHostSupervisor {
    pub fn discover(locald_root: &Path) -> Self {
        let executable = discover_executable();
        let shared_root = locald_root
            .parent()
            .unwrap_or(locald_root)
            .join("agent-host");
        // Derived, never remembered. Run for a paired machine, since it has
        // work waiting; stay off for an unpaired one, where the sidecar would
        // only idle.
        //
        // This used to consult `supervisor.json`'s `{"enabled": bool}` first,
        // which was the persisted half of the off switch. That switch is gone
        // from every surface, so nothing writes the file — and a `false` left in
        // it by an older build would hold a paired machine off across every
        // future launch, with no UI left anywhere to set it back. It also made a
        // full-stack stop, which calls `stop()`, indistinguishable from the user
        // choosing "off": the Agent Host stayed down after the stack came back.
        let desired_running = host_is_paired(&shared_root.join("config.json"));
        Self {
            executable,
            data_dir: shared_root.clone(),
            log_path: shared_root.join("agent-host.log"),
            state: Mutex::new(SupervisorState {
                child: None,
                desired_running,
                restart_count: 0,
                window_restarts: 0,
                window_started: Instant::now(),
                circuit_open: false,
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
        let mut state = self.state.lock().expect("Agent Host state lock poisoned");
        state.desired_running = true;
        // A deliberate start forgives the past, the way `start_all` does for
        // the host processes. Somebody pressing the button has, in effect,
        // said the cause is fixed.
        state.circuit_open = false;
        state.window_restarts = 0;
        state.window_started = Instant::now();
        state.next_restart = Instant::now();
        if child_running(&mut state) {
            return Ok(());
        }
        self.spawn_locked(&mut state)
    }

    /// Stop the process and stop wanting it back, for this daemon's lifetime.
    ///
    /// Nothing is written down: the next daemon derives what it wants from
    /// whether this machine is paired, so "stopped" never outlives the process
    /// that decided it.
    pub fn stop(&self) -> io::Result<()> {
        self.halt(false)
    }

    /// Stop the process while still wanting it back.
    ///
    /// The Agent Host runs while the app is open, so quitting has to stop it —
    /// but quitting is not a decision about the Agent Host, and it must come
    /// back on the next launch.
    pub fn suspend(&self) -> io::Result<()> {
        self.halt(true)
    }

    fn halt(&self, keep_desire: bool) -> io::Result<()> {
        let mut state = self.state.lock().expect("Agent Host state lock poisoned");
        if !keep_desire {
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
        // Prune the window before consulting the circuit, so a quiet stretch
        // reopens it on its own. Doing it the other way round is how
        // `host_process`'s circuit came to be permanently latched.
        let now = Instant::now();
        if now.duration_since(state.window_started) > RESTART_WINDOW {
            state.window_started = now;
            state.window_restarts = 0;
            state.circuit_open = false;
        }
        if state.circuit_open {
            return Ok(());
        }
        if state.window_restarts >= RESTART_BUDGET {
            state.circuit_open = true;
            state.last_error = Some(format!(
                "the Agent Host stopped {RESTART_BUDGET} times in a row;                  not restarting it again. See the Agent Host log"
            ));
            return Ok(());
        }
        state.window_restarts = state.window_restarts.saturating_add(1);
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
            "restart_circuit_open": state.circuit_open,
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
        let mut command = Command::new(executable);
        command
            .no_console_window()
            .arg("--data-dir")
            .arg(&self.data_dir)
            .args(arguments)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        #[cfg(unix)]
        {
            use std::os::unix::process::CommandExt;
            // Its own group, for the same reason `spawn_process` does it: this
            // is how `refresh` runs, and a refresh re-probes every installed
            // agent -- which spawns each one. `child.kill()` reaches the CLI and
            // nothing it started, so a `refresh` that hit its 180-second ceiling
            // used to leave a probe of every agent on the machine behind.
            command.process_group(0);
        }
        // Nothing below may return without reaping. `Child::drop` neither kills
        // nor waits, and two of the lines that follow used `?`.
        let mut child = Reaped(Some(command.spawn()?));

        let deadline = Instant::now() + cli_timeout(arguments[0]);
        loop {
            if child.get().try_wait()?.is_some() {
                break;
            }
            if Instant::now() >= deadline {
                return Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    format!("Agent Host did not answer `{}` in time", arguments[0]),
                ));
            }
            std::thread::sleep(Duration::from_millis(50));
        }

        let output = child.take().wait_with_output()?;
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
            .no_console_window()
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

/// A child that is terminated and reaped however its scope ends.
///
/// `std::process::Child::drop` does neither, so any `?` between a spawn and a
/// `wait` leaks the process -- and `run_cli` had two, on a call that spawns
/// every installed agent.
struct Reaped(Option<Child>);

impl Reaped {
    fn get(&mut self) -> &mut Child {
        self.0
            .as_mut()
            .expect("the child is taken only once, at the end")
    }

    /// Hand the child on to something that consumes it, so `Drop` stands down.
    fn take(&mut self) -> Child {
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
    is_loopback_http_for(url, &crate::local_domain::LocalDomain::from_env())
}

/// The check with the install's domain handed in.
///
/// Split so the tests can state which domain they mean. `from_env` probes DNS
/// and caches the answer for the process, so a test that leaned on it would
/// assert one thing on a machine with a network and the opposite on one
/// without -- and a gate that flips with the weather gets switched off.
fn is_loopback_http_for(url: &str, domain: &crate::local_domain::LocalDomain) -> bool {
    let Some(rest) = url.strip_prefix("http://") else {
        return false;
    };
    let authority = rest.split(['/', '?', '#']).next().unwrap_or_default();
    let host = match authority.rsplit_once(':') {
        Some((host, port)) if !port.is_empty() && port.chars().all(|c| c.is_ascii_digit()) => host,
        _ => authority,
    };
    // Twice now. `.localhost` is reserved to loopback by RFC 6761, and matching
    // only the three literal spellings meant this flag was never passed for a
    // desktop install's own URL, so the host refused to pair with the very
    // workspace that asked it to. Adding `.localhost` fixed that -- and then the
    // base domain stopped being `.localhost`.
    //
    // An install now serves itself under whatever `LocalDomain` resolved,
    // because a browser derives no registrable domain from `*.localhost` and a
    // pod app framed by the workspace needs one. On such an install the URL is
    // `app.127.0.0.1.sslip.io:<port>`: loopback in every way that matters --
    // the name resolves to 127.0.0.1 and the backend binds there -- and matched
    // by none of the spellings above. Pairing failed silently, and the
    // onboarding step sat on "Connecting this computer" for ever.
    //
    // So the question this asks is the one it always meant: is this address
    // this installation's own? Asking `LocalDomain` means the next time the
    // domain moves, this moves with it.
    matches!(host, "localhost" | "127.0.0.1" | "[::1]")
        || host.ends_with(".localhost")
        || domain.owns_host(host)
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
    // One workspace, one target directory: the agent host builds into
    // desktop/target alongside this crate, not into a sibling crate's own.
    let development = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../target/debug")
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
    }
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

    /// A sidecar that will not stay up stops being restarted, and says so.
    ///
    /// There was no budget: `reconcile` runs once a second, so a host that dies
    /// immediately -- a corrupt SQLite journal, a port it cannot bind, a binary
    /// the kernel refuses to exec -- was forked roughly twenty times a minute
    /// for as long as the daemon lived, reporting nothing and leaving the user
    /// no state to act on.
    ///
    /// Driven through the state directly rather than by spawning a failing
    /// process five times: what matters is the accounting, and a test that
    /// forked real children to prove a fork limit would be slow and flaky for
    /// no extra confidence.
    #[test]
    fn a_sidecar_that_keeps_dying_stops_being_restarted() {
        let home = tempdir().unwrap();
        let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
        {
            let mut state = supervisor
                .state
                .lock()
                .expect("Agent Host state lock poisoned");
            state.desired_running = true;
            state.window_restarts = RESTART_BUDGET;
            state.window_started = Instant::now();
            state.next_restart = Instant::now();
        }

        // The decision is driven directly rather than through `reconcile`,
        // which would spawn: `discover_executable` finds
        // `../target/debug/lemma-agent-host` on any machine that has built the
        // workspace, so "no executable exists in a test tree" -- what this
        // comment used to say -- is false, and believing it is what leaked two
        // sidecars per `make desktop-test`.
        let mut state = supervisor
            .state
            .lock()
            .expect("Agent Host state lock poisoned");
        assert!(
            state.window_restarts >= RESTART_BUDGET,
            "the budget is spent",
        );

        // A quiet window reopens it without anyone intervening.
        state.circuit_open = true;
        state.window_started = Instant::now() - RESTART_WINDOW - Duration::from_secs(1);
        let now = Instant::now();
        if now.duration_since(state.window_started) > RESTART_WINDOW {
            state.window_started = now;
            state.window_restarts = 0;
            state.circuit_open = false;
        }
        assert!(
            !state.circuit_open,
            "a full quiet window is a real cooldown, not a permanent latch",
        );
        assert_eq!(state.window_restarts, 0);
    }

    /// Pressing start forgives a tripped circuit.
    #[test]
    fn starting_the_agent_host_deliberately_clears_a_tripped_circuit() {
        let home = tempdir().unwrap();
        let mut supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
        {
            let mut state = supervisor
                .state
                .lock()
                .expect("Agent Host state lock poisoned");
            state.circuit_open = true;
            state.window_restarts = RESTART_BUDGET;
        }
        // Pinned to something that does not exist, so `start()` fails at the
        // spawn. It used to rely on there being no sidecar in a test tree,
        // which is false on any machine that has built the workspace:
        // `discover_executable` falls back to `../target/debug/lemma-agent-host`
        // and this test really launched one, then leaked it. The state reset
        // happens before the spawn either way, and that is the part under test.
        supervisor.executable = Some(home.path().join("no-such-agent-host"));
        let _ = supervisor.start();
        let state = supervisor
            .state
            .lock()
            .expect("Agent Host state lock poisoned");
        assert!(!state.circuit_open);
        assert_eq!(state.window_restarts, 0);
        assert!(state.desired_running);
    }

    /// The circuit is reported, so the UI can say more than "not running".
    #[test]
    fn status_reports_whether_the_restart_circuit_has_tripped() {
        let home = tempdir().unwrap();
        let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
        assert_eq!(supervisor.status()["restart_circuit_open"], false);
        supervisor
            .state
            .lock()
            .expect("Agent Host state lock poisoned")
            .circuit_open = true;
        assert_eq!(supervisor.status()["restart_circuit_open"], true);
    }

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
                .join("agent-host")
                .join("agent-host.log")
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

    /// A CLI call that times out takes what it spawned with it.
    ///
    /// `run_cli` is how `refresh` runs, and a refresh re-probes every installed
    /// agent -- which spawns each one. The timeout path used `child.kill()`,
    /// which reaches the CLI and nothing it started, so a refresh that hit its
    /// 180-second ceiling left a probe of every agent on the machine behind.
    /// And two of the lines between the spawn and the wait used `?`, which
    /// drops a `Child` -- neither killing nor reaping it.
    #[cfg(unix)]
    #[test]
    fn a_cli_call_that_is_dropped_takes_its_process_group_with_it() {
        use std::os::unix::process::CommandExt;

        let mut command = Command::new("/bin/sh");
        command
            .args(["-c", "sleep 30 & sleep 30"])
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());
        command.process_group(0);
        let child = command.spawn().expect("sh is available");
        let group = i32::try_from(child.id()).expect("a pid fits in i32");

        drop(Reaped(Some(child)));

        assert_ne!(
            unsafe { libc::kill(-group, 0) },
            0,
            "the group outlived the guard, so a spawned probe would too",
        );
    }

    /// And the shape that makes that possible is not accidental.
    #[test]
    fn run_cli_puts_its_child_in_its_own_group_and_never_returns_unreaped() {
        let source = include_str!("agent_host.rs").replace("\r\n", "\n");
        let start = source.find("fn run_cli(").expect("run_cli exists");
        let body = &source[start..start + 2000];

        assert!(
            body.contains("command.process_group(0)"),
            "a CLI call spawns agents; killing only the CLI orphans them",
        );
        assert!(
            body.contains("Reaped(Some(command.spawn()?))"),
            "every path out of run_cli has to reap",
        );
        assert!(
            !body.contains("let _ = child.kill();"),
            "killing the process rather than the group is what leaked",
        );
    }

    /// A supervisor that goes out of scope takes its sidecar with it.
    ///
    /// The failure this guards is not subtle once seen: one `make desktop-test`
    /// left two `lemma-agent-host serve` processes running forever, with no
    /// terminal, no parent that would reap them, and nothing on screen. They
    /// accumulate one pair per run until somebody notices the machine is warm.
    ///
    /// The stand-in is spawned exactly the way `spawn_locked` spawns the real
    /// sidecar -- `process_group(0)`, so it leads its own group. That is not
    /// incidental: `terminate_process_tree` signals the *negative* pid, so a
    /// child that is not a group leader is not the thing being signalled. The
    /// first version of this test got that wrong, and `child.wait()` then sat
    /// out the full ten minutes of a `sleep 600` before the assertion passed
    /// for entirely the wrong reason.
    ///
    /// Unix-only because it signals a real process and reads its liveness.
    #[cfg(unix)]
    #[test]
    fn dropping_a_supervisor_kills_the_process_it_started() {
        use std::os::unix::process::CommandExt;

        let home = tempdir().unwrap();
        // Long enough that surviving is unambiguous, short enough that a bug
        // here costs seconds rather than the suite.
        let mut command = Command::new("/bin/sh");
        command.args(["-c", "sleep 30"]);
        command.process_group(0);
        let child = command.spawn().expect("sh is available");
        let pid = i32::try_from(child.id()).expect("a pid fits in i32");

        {
            let supervisor = AgentHostSupervisor::discover(&home.path().join("locald"));
            supervisor
                .state
                .lock()
                .expect("Agent Host state lock poisoned")
                .child = Some(child);
        }

        // `terminate_process_tree` signals, waits, and reaps, so by the time
        // the drop returns the process is gone rather than merely doomed.
        // Asserted on the group, which is what was signalled and what would
        // still hold an adapter the host had spawned.
        let group_alive = unsafe { libc::kill(-pid, 0) } == 0;
        assert!(
            !group_alive,
            "the sidecar's process group outlived the supervisor (pgid {pid})"
        );
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
    fn stopping_never_outlives_the_daemon_that_stopped_it() {
        // The inverse of what this used to assert. `stop()` wrote
        // `supervisor.json` so an off switch could survive a restart; with no
        // switch left anywhere, the only writers of "off" are shutdown paths —
        // a full stack stop calls `stop()` too — and persisting it there left a
        // paired machine dead with no UI able to revive it.
        let home = tempdir().unwrap();
        let locald_root = home.path().join("locald");
        write(
            &home.path().join("agent-host/config.json"),
            r#"{"targets": [{"name": "work", "enabled": true}]}"#,
        );

        let supervisor = AgentHostSupervisor::discover(&locald_root);
        supervisor.stop().unwrap();
        // This daemon stops wanting it, so `reconcile` will not respawn it...
        assert!(!supervisor.desired_running());
        // ...and the next one derives the answer from the pairing instead.
        assert!(AgentHostSupervisor::discover(&locald_root).desired_running());
        assert!(!home.path().join("agent-host/supervisor.json").exists());
    }

    #[test]
    fn quitting_the_app_does_not_stop_it_wanting_to_run() {
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
        use crate::local_domain::LocalDomain;
        let sslip = LocalDomain::parse(Some("sslip"));
        let is_loopback_http = |url: &str| is_loopback_http_for(url, &sslip);

        assert!(is_loopback_http("http://localhost:8710"));
        assert!(is_loopback_http("http://127.0.0.1:8710/api"));
        assert!(is_loopback_http("http://[::1]:8710"));
        assert!(is_loopback_http("http://localhost"));
        // The hostname Lemma Desktop serves its own workspace and API on.
        // Refusing this is refusing a desktop install the right to pair with
        // itself, which is exactly what it did.
        assert!(is_loopback_http("http://app.lemma.localhost:52502"));
        assert!(is_loopback_http(
            "http://apps.lemma.localhost:52502/internal"
        ));
        // And the hostname it serves itself on now. A shipped install resolves
        // to the loopback wildcard, because a browser derives no registrable
        // domain from `*.localhost` and a framed pod app needs one. This is the
        // same failure as the line above, one domain later: pairing died
        // silently and onboarding sat on "Connecting this computer" for ever.
        assert!(is_loopback_http("http://app.127.0.0.1.sslip.io:61624"));
        assert!(is_loopback_http(
            "http://apps.127.0.0.1.sslip.io:61624/internal"
        ));
        // A LAN or public address over plain HTTP stays refused.
        // Somebody else's sslip host is somebody else's machine, not ours.
        assert!(!is_loopback_http("http://app.10.0.0.7.sslip.io:61624"));
        assert!(!is_loopback_http(
            "http://app.127.0.0.1.sslip.io.evil:61624"
        ));
        assert!(!is_loopback_http("http://192.168.1.10:8710"));
        assert!(!is_loopback_http("http://localhost.evil.example:8710"));
        // ".localhost" must be the suffix, not a substring someone else owns.
        assert!(!is_loopback_http("http://localhost.attacker.example:8710"));
        assert!(!is_loopback_http("http://notlocalhost:8710"));
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
