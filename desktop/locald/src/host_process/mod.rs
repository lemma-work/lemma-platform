//! The processes locald supervises on the host: the backend, the frontend,
//! the one-time setups before them, and the ledger that survives a replaced
//! daemon.
//!
//! Was one 4,688-line file. Split by what the manager is doing to them.

use crate::port_reservation::PortReservation;
// Only the Windows stop path needs it; elsewhere flags go on directly.
#[cfg(windows)]
use crate::NoConsoleWindow;
use std::collections::{HashMap, HashSet, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::net::{IpAddr, SocketAddr, TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

const MANIFEST_SCHEMA_VERSION: u64 = 1;
const PROCESS_LEDGER_SCHEMA_VERSION: u64 = 1;
const REQUIRED_SERVICES: [&str; 2] = ["backend", "frontend"];
const REQUIRED_SETUPS: [&str; 1] = ["migrations"];

mod environment;
mod health;
mod ledger;
mod lifecycle;
mod logs;
mod manifest;
mod ports;
mod process_identity;
mod setups;
mod spawn;
mod status;
mod supervision;

pub(crate) use environment::*;
pub(crate) use health::*;
pub(crate) use ledger::*;
pub(crate) use logs::*;
pub(crate) use manifest::*;
pub(crate) use ports::*;
pub(crate) use process_identity::*;
pub(crate) use spawn::*;
pub(crate) use status::*;

#[cfg(test)]
mod tests;

pub(crate) struct ManagedChild {
    pub(crate) child: Child,
    pub(crate) started_at: Instant,
}

#[derive(Default)]
pub(crate) struct ProcessState {
    pub(crate) children: HashMap<String, ManagedChild>,
    pub(crate) restart_history: HashMap<String, VecDeque<Instant>>,
    pub(crate) restart_not_before: HashMap<String, Instant>,
    pub(crate) circuit_open: HashSet<String>,
    /// How many times each component has tripped its restart circuit.
    ///
    /// `circuit_open` now flickers -- it closes after a quiet window so a
    /// transient burst is survivable -- which on its own would let a flapping
    /// service oscillate the UI between "error" and "starting". This is the
    /// durable half: once a component has tripped, it keeps reading as failed
    /// until something clears it deliberately.
    pub(crate) circuit_trips: HashMap<String, u32>,
    pub(crate) last_exit: HashMap<String, String>,
}

pub struct HostProcessManager {
    manifest: HostPackManifest,
    ordered_ids: Vec<String>,
    by_id: HashMap<String, HostProcessSpec>,
    state: Mutex<ProcessState>,
    backend_environment: Mutex<HashMap<String, String>>,
    service_environment: Mutex<HashMap<String, HashMap<String, String>>>,
    desired_running: AtomicBool,
    health_ready: AtomicBool,
    startup_in_progress: AtomicBool,
    dependency_ready: AtomicBool,
    dependency_error: Mutex<Option<String>>,
    idle_port_reservations: Mutex<HashMap<u16, PortReservation>>,
    runtime_generation: Mutex<String>,
    generation_prepared: AtomicBool,
    process_ledger_path: PathBuf,
    process_ledger_lock: Mutex<()>,
    reconcile_lock: Mutex<()>,
    installation_id: String,
    #[cfg(windows)]
    windows_job: usize,
    log_dir: PathBuf,
}

/// Never outlive the services this manager started.
///
/// Was `#[cfg(windows)]` and closed only the job handle, so on macOS and Linux
/// nothing stopped the children at all -- and `stop_all()` on the success path
/// was the only thing that ever did. A test that panicked past it left its
/// services running forever: `spawn_command` sets `process_group(0)`, so
/// closing the terminal sends them no `SIGHUP`, and the ownership ledger that
/// could reclaim them lives in a `TempDir` that is already gone. Two immortal
/// `sh` loops per failed run, each forking a `sleep` every second.
///
/// This could not have fired before regardless: the monitor thread held an
/// `Arc` of this type, so the refcount never reached zero. See `Arc::downgrade`
/// above.
impl Drop for HostProcessManager {
    fn drop(&mut self) {
        // Not `stop_all`: this is a backstop, it cannot report anything, and
        // the ports and readiness state it also maintains are about to be
        // dropped anyway. Terminating the process groups is the part that
        // outlives us if it is skipped.
        for id in self.ordered_ids.clone().iter().rev() {
            let _ = self.stop_process(id);
        }
        #[cfg(windows)]
        if self.windows_job != 0 {
            unsafe {
                windows_sys::Win32::Foundation::CloseHandle(self.windows_job as _);
            }
            self.windows_job = 0;
        }
    }
}

impl HostProcessManager {
    pub fn load(path: &Path, log_dir: PathBuf) -> io::Result<Arc<Self>> {
        let raw = std::fs::read_to_string(path)?;
        let manifest: HostPackManifest = serde_json::from_str(&raw).map_err(|error| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("invalid host-pack manifest: {error}"),
            )
        })?;
        Self::new(manifest, log_dir)
    }

    pub fn new(manifest: HostPackManifest, log_dir: PathBuf) -> io::Result<Arc<Self>> {
        Self::build(manifest, log_dir, true)
    }

    /// A manager whose supervisor thread never starts.
    ///
    /// For tests that drive `reconcile_crashes` themselves. The supervisor
    /// calls it once a second, and it is not a passive observer -- it spends
    /// the restart budget and trips the circuit exactly as a manual call does.
    /// A test that crashes a service and then reasons about which crash
    /// exhausted the budget is therefore racing a second driver of the same
    /// state machine.
    ///
    /// The race is not just interleaved bookkeeping. `reconcile_crashes`
    /// decides `ready_to_spawn` under the state lock and then spawns *after
    /// releasing it*, so a supervisor descheduled in that gap carries a
    /// decision made before a crash across to after it, and respawns a service
    /// whose circuit has since opened. That is what CI hit: `!backend.running`
    /// failed because the supervisor had resurrected the backend the test had
    /// just watched trip. Reproduced deterministically by stalling only the
    /// unnamed (supervisor) thread between that decision and the spawn.
    ///
    /// Harmless in production, where the supervisor is the only caller and its
    /// decisions are therefore serial. It is having a second driver that makes
    /// it reachable, and that only ever happens in a test.
    ///
    /// Not a way of avoiding a hard test. That the supervisor restarts a
    /// crashed service unprompted is asserted directly by
    /// `the_supervisor_restarts_a_crashed_service_with_nobody_driving_it`,
    /// which builds a supervised manager and touches nothing. What the circuit
    /// tests are about is the transition table underneath, and that has to be
    /// stepped deliberately to be asserted at all.
    #[cfg(test)]
    fn without_supervisor(manifest: HostPackManifest, log_dir: PathBuf) -> io::Result<Arc<Self>> {
        Self::build(manifest, log_dir, false)
    }

    fn build(
        manifest: HostPackManifest,
        log_dir: PathBuf,
        supervise: bool,
    ) -> io::Result<Arc<Self>> {
        let ordered_ids = validate_and_order(&manifest)?;
        let by_id = manifest
            .services
            .iter()
            .cloned()
            .map(|spec| (spec.id.clone(), spec))
            .collect();
        std::fs::create_dir_all(&log_dir)?;
        let state_root = log_dir
            .parent()
            .ok_or_else(|| io::Error::other("host process log directory has no parent"))?;
        let installation_id = load_or_create_installation_id(state_root)?;
        let process_ledger_path = state_root.join("processes.json");
        reclaim_verified_processes(&process_ledger_path, &installation_id, &manifest)?;
        let idle_port_reservations = reserve_managed_app_ports(&manifest);
        #[cfg(windows)]
        let windows_job = create_windows_job()?;
        let manager = Arc::new(Self {
            manifest,
            ordered_ids,
            by_id,
            state: Mutex::new(ProcessState::default()),
            backend_environment: Mutex::new(HashMap::new()),
            service_environment: Mutex::new(HashMap::new()),
            desired_running: AtomicBool::new(false),
            health_ready: AtomicBool::new(false),
            startup_in_progress: AtomicBool::new(false),
            dependency_ready: AtomicBool::new(true),
            dependency_error: Mutex::new(None),
            idle_port_reservations: Mutex::new(idle_port_reservations),
            runtime_generation: Mutex::new(String::new()),
            generation_prepared: AtomicBool::new(false),
            process_ledger_path,
            process_ledger_lock: Mutex::new(()),
            reconcile_lock: Mutex::new(()),
            installation_id,
            #[cfg(windows)]
            windows_job,
            log_dir,
        });
        // `Weak`, not `Arc`. Holding a strong reference here kept the refcount
        // above zero for the life of the process, which meant this thread ran
        // forever *and* no `Drop` on the manager could ever fire. In a test
        // binary that is 21 threads waking every second -- one per
        // `manager_in` -- each still calling `reconcile_crashes`, which
        // *respawns* a service whose test has already panicked past its
        // `stop_all`. A supervisor nobody owns, re-forking shells.
        if !supervise {
            return Ok(manager);
        }
        let monitor = Arc::downgrade(&manager);
        thread::spawn(move || {
            let mut next_rotation = Instant::now() + SERVICE_LOG_ROTATE_INTERVAL;
            loop {
                thread::sleep(Duration::from_secs(1));
                // The owner is gone, so there is nothing left to supervise and
                // this thread is what was keeping it alive.
                let Some(monitor) = monitor.upgrade() else {
                    return;
                };
                monitor.reconcile_crashes();
                // Separate cadence from the crash check on purpose: this stats
                // a file per service and nothing here needs it every second.
                if Instant::now() >= next_rotation {
                    monitor.rotate_service_logs();
                    next_rotation = Instant::now() + SERVICE_LOG_ROTATE_INTERVAL;
                }
            }
        });
        Ok(manager)
    }
}
