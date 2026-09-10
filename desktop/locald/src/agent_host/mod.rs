//! The Agent Host sidecar, supervised by locald.
//!
//! Was one 1,564-line file.

use crate::NoConsoleWindow;
use std::fs::{File, OpenOptions};
use std::io;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::Mutex;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use serde_json::{json, Value};

mod cli;
mod lifecycle;
mod pairing;
mod process;
mod status;

pub(crate) use cli::*;
pub(crate) use pairing::*;
pub(crate) use process::*;

#[cfg(test)]
mod tests;

pub(crate) const LOG_LIMIT_BYTES: u64 = 5 * 1024 * 1024;
pub(crate) const RESTART_BACKOFF: Duration = Duration::from_secs(3);
/// How many restarts inside one window before the supervisor stops trying.
///
/// There was no budget at all: `reconcile` runs once a second, and a sidecar
/// that dies immediately -- a corrupt SQLite journal, a port it cannot bind, a
/// binary the kernel refuses to exec -- was forked roughly twenty times a
/// minute for as long as the daemon lived, with nothing reported and no state
/// the user could act on. `host_process` has had a circuit breaker for its
/// services all along; this is the same idea for the one supervisor that
/// lacked it.
pub(crate) const RESTART_BUDGET: u32 = 5;
/// The window the budget is counted over, and the cooldown before the circuit
/// closes again. Long enough that a genuinely broken host stops hammering,
/// short enough that a transient cause -- a port briefly held by the previous
/// process -- recovers on its own.
pub(crate) const RESTART_WINDOW: Duration = Duration::from_secs(60);

#[derive(Debug)]
pub(crate) struct SupervisorState {
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
    /// Held by whoever is starting or stopping the sidecar; never by anyone
    /// only reporting on it.
    ///
    /// Terminating the process tree is a five-second `SIGTERM` wait before it
    /// is a `SIGKILL`, and reclaiming a leftover is another. Those used to
    /// happen under `state`, which `status` also takes -- so stopping the
    /// Agent Host froze the tray menu it was stopped from, for up to eleven
    /// seconds. This serialises the transitions instead, and `state` is held
    /// only long enough to read or write a field.
    ///
    /// Lock order: this one before `state`, never the other way.
    transition: Mutex<()>,
    executable: Option<PathBuf>,
    data_dir: PathBuf,
    log_path: PathBuf,
    /// Where the running sidecar's identity is written down, so the next
    /// daemon can recognise one this daemon did not live to stop.
    record_path: PathBuf,
    /// This installation, so a record written by another one is never acted on.
    installation_id: Option<String>,
    state: Mutex<SupervisorState>,
    details: Mutex<Option<(Instant, Value)>>,
}

/// What was running, written down while it runs.
///
/// The sidecar takes an exclusive lock on its data directory and refuses to
/// start when something else holds it -- correctly, since two of them fight
/// over one pairing. The lock is released when its holder dies, and the code
/// that takes it says there is "nothing stale to clean up after a crash",
/// which is true of a crash and not of the case that actually happened: the
/// sidecar *outlived* the daemon that started it, was reparented to init, and
/// went on holding the lock. Every launch after that logged
/// `another Agent Host is already serving` and retried, forever -- 3,295 lines
/// of it in one installation -- while chat answered "Fetch is aborted" and
/// nothing anywhere named the cause.
///
/// `Drop` on the supervisor covers the ordinary exits. It cannot cover the
/// ones that matter here: the daemon killed outright, or the machine losing
/// power. So the running process is recorded, and the next daemon reclaims
/// what it finds before starting its own.
#[derive(serde::Serialize, serde::Deserialize)]
pub(crate) struct AgentHostRecord {
    schema_version: u32,
    installation_id: String,
    pid: u32,
    executable: String,
    start_identity: String,
}

pub(crate) const AGENT_HOST_RECORD_SCHEMA_VERSION: u32 = 1;

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
            transition: Mutex::new(()),
            executable,
            data_dir: shared_root.clone(),
            log_path: shared_root.join("agent-host.log"),
            record_path: shared_root.join("serve-process.json"),
            // Best effort: without it nothing is reclaimed, which is the
            // behaviour that existed before this record did. It is never a
            // reason to fail to start.
            installation_id: crate::host_process::installation_identity(locald_root).ok(),
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
}
