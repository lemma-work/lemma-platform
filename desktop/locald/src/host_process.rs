// Only the Windows stop path needs it; elsewhere flags go on directly.
#[cfg(windows)]
use crate::NoConsoleWindow;
use std::collections::{HashMap, HashSet, VecDeque};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Write};
use std::net::{IpAddr, Ipv4Addr, SocketAddr, TcpListener, TcpStream, ToSocketAddrs};
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
const INHERITED_ENVIRONMENT: [&str; 24] = [
    "PATH",
    "HOME",
    "USERPROFILE",
    "LOCALAPPDATA",
    "APPDATA",
    "PROGRAMDATA",
    "SystemRoot",
    "SYSTEMROOT",
    "WINDIR",
    "COMSPEC",
    "PATHEXT",
    "TEMP",
    "TMP",
    "TMPDIR",
    "LANG",
    "LC_ALL",
    "TZ",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
    "ALL_PROXY",
    "SSH_AUTH_SOCK",
];

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostPackManifest {
    pub schema_version: u64,
    pub release: String,
    #[serde(default)]
    pub managed_runtime: Option<ManagedRuntimeSpec>,
    pub setup: Vec<HostSetupSpec>,
    pub services: Vec<HostProcessSpec>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedRuntimeSpec {
    pub images: ManagedRuntimeImages,
    pub credentials: ManagedRuntimeCredentials,
    pub ports: ManagedRuntimePorts,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedRuntimeImages {
    pub postgres: String,
    pub redis: String,
    pub supertokens: String,
    /// The sandbox images, warmed at start rather than on first use.
    ///
    /// Optional because a host pack written before this carries neither, and a
    /// missing warm-up is a slower first run rather than a broken install.
    #[serde(default)]
    pub workspace: Option<String>,
    #[serde(default)]
    pub function: Option<String>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedRuntimeCredentials {
    pub postgres_password: String,
    pub redis_password: String,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct ManagedRuntimePorts {
    pub postgres: u16,
    pub redis: u16,
    pub supertokens: u16,
    pub backend: u16,
    pub frontend: u16,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostSetupSpec {
    pub id: String,
    pub command: Vec<String>,
    #[serde(default)]
    pub cwd: Option<PathBuf>,
    #[serde(default)]
    pub env: HashMap<String, String>,
    #[serde(default = "default_setup_timeout")]
    pub timeout_seconds: u64,
    #[serde(default = "default_setup_max_attempts")]
    pub max_attempts: usize,
    #[serde(default = "default_setup_retry_backoff")]
    pub retry_backoff_seconds: u64,
    /// Whether failing this step should stop the whole stack from starting.
    ///
    /// Migrations are not optional: a backend running against a schema it does
    /// not expect is worse than one that refuses to start. Seeding the
    /// connector catalog is — it reaches the network when a Composio key is
    /// set, and a workspace that cannot start because a third-party catalog
    /// was unreachable would be a bad trade for a feature the user may not be
    /// using in this session.
    #[serde(default)]
    pub optional: bool,
    /// What this setup's result depends on. Re-run only when it changes.
    ///
    /// Both setups run on *every* start today, and the cost is not the SQL.
    /// Alembic's no-op is one `SELECT`; the expense is `migrations/env.py`
    /// importing the whole ORM graph -- several thousand modules -- before it
    /// can decide there is nothing to do. `connector-catalog` is worse: it
    /// re-upserts the entire native catalog every time, with a ten-minute
    /// budget.
    ///
    /// The renderer sets this to something that changes exactly when the work
    /// would produce a different result, so a warm start skips both. Absent, or
    /// changed, the setup runs -- so a pack that predates this, or a manifest
    /// that declines to declare one, behaves exactly as before.
    #[serde(default)]
    pub stamp: Option<String>,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HostProcessSpec {
    pub id: String,
    pub command: Vec<String>,
    #[serde(default)]
    pub cwd: Option<PathBuf>,
    #[serde(default)]
    pub env: HashMap<String, String>,
    #[serde(default)]
    pub dependencies: Vec<String>,
    #[serde(default)]
    pub health: Option<HttpHealthSpec>,
    #[serde(default)]
    pub restart: RestartSpec,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct HttpHealthSpec {
    pub url: String,
    #[serde(default = "default_health_timeout")]
    pub timeout_seconds: u64,
    #[serde(default)]
    pub expected_body: Option<String>,
    #[serde(default)]
    pub stabilization_seconds: u64,
}

#[derive(Clone, Debug, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct RestartSpec {
    #[serde(default = "default_max_restarts")]
    pub max_restarts: usize,
    #[serde(default = "default_restart_window")]
    pub window_seconds: u64,
    #[serde(default = "default_restart_backoff")]
    pub backoff_seconds: u64,
}

impl Default for RestartSpec {
    fn default() -> Self {
        Self {
            max_restarts: default_max_restarts(),
            window_seconds: default_restart_window(),
            backoff_seconds: default_restart_backoff(),
        }
    }
}

/// Whether a stamped setup has already been done, for this exact stamp.
///
/// A free function so it can be asserted on every platform. The tests that
/// exercise it end to end have to spawn `/bin/sh`, so they are `#[cfg(unix)]` --
/// and gating them left Windows covering none of this, which is the wrong trade
/// for a decision that is pure and is the whole point of the feature.
///
/// No stamp means always run: that is how a setup opts out, and it is what
/// everything did before stamps existed. A stamp that differs from the recorded
/// one means the work is not the work that was done -- a new pack release, or
/// migrations that changed within one.
fn setup_is_already_done(stamp: Option<&str>, recorded: Option<&String>) -> bool {
    match stamp {
        None => false,
        Some(stamp) => recorded.map(String::as_str) == Some(stamp),
    }
}

fn default_health_timeout() -> u64 {
    180
}

fn default_setup_timeout() -> u64 {
    300
}

fn default_setup_max_attempts() -> usize {
    3
}

fn default_setup_retry_backoff() -> u64 {
    2
}

fn default_max_restarts() -> usize {
    3
}

fn default_restart_window() -> u64 {
    60
}

fn default_restart_backoff() -> u64 {
    2
}

struct ManagedChild {
    child: Child,
    started_at: Instant,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ProcessLedger {
    schema_version: u64,
    installation_id: String,
    entries: Vec<ProcessLedgerEntry>,
}

#[derive(Clone, Debug, Deserialize, Serialize)]
#[serde(deny_unknown_fields)]
struct ProcessLedgerEntry {
    service_id: String,
    pid: u32,
    executable: String,
    start_identity: String,
    installation_id: String,
    runtime_generation: String,
}

pub(crate) struct ProcessIdentity {
    pub(crate) executable: String,
    pub(crate) start_identity: String,
}

#[derive(Default)]
struct ProcessState {
    children: HashMap<String, ManagedChild>,
    restart_history: HashMap<String, VecDeque<Instant>>,
    restart_not_before: HashMap<String, Instant>,
    circuit_open: HashSet<String>,
    /// How many times each component has tripped its restart circuit.
    ///
    /// `circuit_open` now flickers -- it closes after a quiet window so a
    /// transient burst is survivable -- which on its own would let a flapping
    /// service oscillate the UI between "error" and "starting". This is the
    /// durable half: once a component has tripped, it keeps reading as failed
    /// until something clears it deliberately.
    circuit_trips: HashMap<String, u32>,
    last_exit: HashMap<String, String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct HostProcessStatus {
    pub id: String,
    pub running: bool,
    pub pid: Option<u32>,
    pub circuit_open: bool,
    /// Times this component has exhausted its restart budget since the last
    /// deliberate start. Survives the circuit closing, so a service that keeps
    /// flapping does not read as healthy between bursts.
    pub circuit_trips: u32,
    pub restart_count: usize,
    pub last_exit: Option<String>,
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
    idle_port_reservations: Mutex<HashMap<u16, TcpListener>>,
    runtime_generation: Mutex<String>,
    generation_prepared: AtomicBool,
    process_ledger_path: PathBuf,
    process_ledger_lock: Mutex<()>,
    installation_id: String,
    #[cfg(windows)]
    windows_job: usize,
    log_dir: PathBuf,
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
        let idle_port_reservations = reserve_managed_app_ports(&manifest)?;
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

    pub fn release(&self) -> &str {
        &self.manifest.release
    }

    pub fn managed_runtime(&self) -> Option<&ManagedRuntimeSpec> {
        self.manifest.managed_runtime.as_ref()
    }

    pub fn application_ports(&self) -> Option<(u16, u16)> {
        if let Some(runtime) = self.manifest.managed_runtime.as_ref() {
            return Some((runtime.ports.frontend, runtime.ports.backend));
        }
        let frontend = self
            .by_id
            .get("frontend")
            .and_then(|service| service.health.as_ref())
            .and_then(|health| loopback_http_port(&health.url));
        let backend = self
            .by_id
            .get("backend")
            .and_then(|service| service.health.as_ref())
            .and_then(|health| loopback_http_port(&health.url));
        frontend.zip(backend)
    }

    pub fn desired_running(&self) -> bool {
        self.desired_running.load(Ordering::Acquire)
    }

    pub fn backend_restart_available(&self) -> bool {
        self.inspect_exits();
        self.dependency_ready.load(Ordering::Acquire)
            && !self.startup_in_progress.load(Ordering::Acquire)
            && self
                .state
                .lock()
                .expect("host process lock poisoned")
                .children
                .contains_key("backend")
    }

    pub fn set_backend_environment(&self, environment: HashMap<String, String>) {
        *self
            .backend_environment
            .lock()
            .expect("backend environment lock poisoned") = environment;
    }

    pub fn replace_service_environment(
        &self,
        service: &str,
        environment: HashMap<String, String>,
    ) -> HashMap<String, String> {
        let mut overlays = self
            .service_environment
            .lock()
            .expect("service environment lock poisoned");
        if environment.is_empty() {
            overlays.remove(service).unwrap_or_default()
        } else {
            overlays
                .insert(service.to_owned(), environment)
                .unwrap_or_default()
        }
    }

    pub fn service_environment(&self, service: &str) -> HashMap<String, String> {
        self.service_environment
            .lock()
            .expect("service environment lock poisoned")
            .get(service)
            .cloned()
            .unwrap_or_default()
    }

    pub fn mark_dependency_ready(&self) {
        self.dependency_ready.store(true, Ordering::Release);
        *self
            .dependency_error
            .lock()
            .expect("dependency error lock poisoned") = None;
    }

    pub fn mark_dependency_recovering(&self) {
        self.dependency_ready.store(false, Ordering::Release);
        *self
            .dependency_error
            .lock()
            .expect("dependency error lock poisoned") = None;
    }

    pub fn mark_dependency_unavailable(&self, message: String) {
        self.dependency_ready.store(false, Ordering::Release);
        *self
            .dependency_error
            .lock()
            .expect("dependency error lock poisoned") = Some(message);
    }

    pub fn start_all(&self) -> io::Result<()> {
        self.start_all_with_progress(|_| {})
    }

    pub fn start_all_with_progress(&self, mut progress: impl FnMut(&str)) -> io::Result<()> {
        if self
            .startup_in_progress
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return Err(io::Error::new(
                io::ErrorKind::WouldBlock,
                "host process startup is already running",
            ));
        }
        let result = self.start_all_inner(&mut progress);
        self.startup_in_progress.store(false, Ordering::Release);
        result
    }

    pub fn prepare_runtime_generation(&self) -> io::Result<String> {
        self.inspect_exits();
        // The same question `start_all_inner` asks, asked the same way. These
        // two used to disagree on a *partially* running stack: this one saw
        // "some children" and kept the old generation, while `start_all_inner`
        // saw "not all children" and minted a new one. Every phase, state and
        // ready event then carried the old value while the manager ran the new
        // one -- and the generation is not cosmetic. It is injected into each
        // service at spawn and checked on the next launch to decide whether the
        // recorded workspace is still the one serving, so a mixture of old and
        // new made that check answerable by processes from two different runs.
        let fully_up = self.stack_is_fully_up();
        let mut generation = self
            .runtime_generation
            .lock()
            .expect("runtime generation lock poisoned");
        if !fully_up {
            *generation = random_generation()?;
            self.generation_prepared.store(true, Ordering::Release);
        }
        Ok(generation.clone())
    }

    /// Whether nothing at all is running.
    fn state_is_empty(&self) -> bool {
        self.state
            .lock()
            .expect("host process lock poisoned")
            .children
            .is_empty()
    }

    /// Whether every managed service is currently running.
    ///
    /// Callers must have just called `inspect_exits`, so the child map reflects
    /// processes that have already died.
    fn stack_is_fully_up(&self) -> bool {
        self.state
            .lock()
            .expect("host process lock poisoned")
            .children
            .len()
            == self.ordered_ids.len()
    }

    fn start_all_inner(&self, progress: &mut dyn FnMut(&str)) -> io::Result<()> {
        self.inspect_exits();
        if self.stack_is_fully_up() {
            self.desired_running.store(true, Ordering::Release);
            // Everything is already up, so this is a reconcile, not a start.
            // `verify_all_health_now` runs the same probes with the same retry
            // budget; what it drops is the per-service stabilization dwell,
            // which is ~2s each, serialized, on every warm launch. That dwell
            // exists to catch a process that dies seconds after it starts
            // listening — a service that has been serving since the last
            // session has already proven it. The cold path below still pays it.
            self.verify_all_health_now()?;
            self.health_ready.store(true, Ordering::Release);
            return Ok(());
        }
        self.health_ready.store(false, Ordering::Release);
        self.desired_running.store(false, Ordering::Release);
        // Survivors of a partial stack are stopped before a new generation is
        // minted. Otherwise `spawn_if_missing` leaves them alone -- they are
        // already running -- while the health gate rewrites its expected body
        // to the new generation, so the live service is rejected as "a
        // different runtime instance" and retried for its whole timeout before
        // the start fails. That is the ordinary recovery path after a backend
        // crash loop: press Start, wait two minutes, get an error that reads
        // like a security failure. Pressing Start again then works, because by
        // then everything is down.
        if !self.state_is_empty() {
            self.stop_all()?;
        }
        if !self.generation_prepared.swap(false, Ordering::AcqRel) {
            *self
                .runtime_generation
                .lock()
                .expect("runtime generation lock poisoned") = random_generation()?;
        }
        {
            let mut state = self.state.lock().expect("host process lock poisoned");
            state.circuit_open.clear();
            // A deliberate start is the one thing that forgives past trips.
            state.circuit_trips.clear();
            state.restart_history.clear();
            state.restart_not_before.clear();
        }

        progress("migrations");
        self.run_setups()?;
        self.desired_running.store(true, Ordering::Release);

        // Spawn first, gate afterwards.
        //
        // This used to spawn a service, wait for it to pass its full health
        // gate, and only then spawn the next — which put the frontend's entire
        // boot after the backend's, for about 2.7s of a 20s cold start that it
        // never needed to wait for. `next start` serves a prebuilt app and does
        // not call the backend to come up; its health check reads a static file
        // it serves itself.
        //
        // Spawning in `ordered_ids` order still honours declared dependencies,
        // and honours them exactly as the supervision loop does: it requires a
        // dependency's *process to exist*, not to be healthy. Readiness is
        // unchanged — every service still passes the same gate in the same
        // order before this returns.
        // A spawn failure is held rather than returned, so that a service which
        // started and then died still gets to report its exit status and log
        // first. Spawning concurrently means both can be true at once, and the
        // process that crashed is nearly always the more useful answer than the
        // one that could not start because of it.
        let mut spawn_failure = None;
        for id in &self.ordered_ids {
            progress(id);
            self.release_idle_port_for(id);
            if let Err(error) = self.spawn_if_missing(id) {
                spawn_failure.get_or_insert((id.clone(), error));
            }
        }
        // Gate every service at once, not one after another.
        //
        // Each gate requires `stabilization_seconds` of *continuously observed*
        // health, and `healthy_since` is local to the call -- so a serial loop
        // charges that dwell once per service. The frontend is ready in about
        // 0.3s and then sits there healthy while the backend's gate runs, and
        // only afterwards does its own gate start watching and spend a fresh
        // two seconds confirming what was already true. Two services, four
        // seconds, for a guard that needs two.
        //
        // Watching them concurrently costs nothing and weakens nothing: every
        // service still proves the same uninterrupted dwell against the same
        // probe. It just stops the clock starting late on services that came up
        // early. This is the same move as spawning before gating above, applied
        // to the half that was still serial.
        //
        // Results are collected in `ordered_ids` order, so the service reported
        // on a failure does not depend on which thread lost the race.
        let gates: Vec<(String, io::Result<()>)> = thread::scope(|scope| {
            let running: Vec<_> = self
                .ordered_ids
                .iter()
                // Nothing to wait for on a service that never started; waiting
                // would just spend its whole health timeout to say so.
                .filter(|id| {
                    !spawn_failure
                        .as_ref()
                        .is_some_and(|(failed, _)| failed == *id)
                })
                .filter_map(|id| self.health_spec(id).map(|health| (id, health)))
                .map(|(id, health)| {
                    scope.spawn(move || (id.clone(), self.wait_process_health(id, &health)))
                })
                .collect();
            running
                .into_iter()
                .map(|handle| handle.join().expect("health gate thread panicked"))
                .collect()
        });
        for (id, result) in gates {
            if let Err(error) = result {
                let _ = self.stop_all();
                return Err(io::Error::other(format!(
                    "{id} failed health gate: {error}"
                )));
            }
        }
        if let Some((_, error)) = spawn_failure {
            let _ = self.stop_all();
            return Err(error);
        }
        if let Err(error) = self.verify_all_health_now() {
            let _ = self.stop_all();
            return Err(error);
        }
        self.health_ready.store(true, Ordering::Release);
        Ok(())
    }

    fn verify_all_health_now(&self) -> io::Result<()> {
        for id in &self.ordered_ids {
            if let Some(mut health) = self.health_spec(id) {
                health.stabilization_seconds = 0;
                self.wait_process_health(id, &health).map_err(|error| {
                    io::Error::other(format!("{id} failed final health gate: {error}"))
                })?;
            }
        }
        Ok(())
    }

    /// Whether any component should be reported as having failed.
    ///
    /// `circuit_trips`, not just `circuit_open`. The circuit now closes on its own
    /// after a quiet window, so reading only the live flag would let a service that
    /// trips once per window report healthy in every gap -- oscillating the splash
    /// between "error" and "starting" while nothing actually improved.
    ///
    /// A free function so this is testable without racing a real supervisor: the
    /// interesting state (circuit closed again, trip remembered, service still
    /// down) exists for a fraction of a second in a live manager.
    fn components_report_failure(components: &[HostProcessStatus]) -> bool {
        components
            .iter()
            .any(|component| component.circuit_open || component.circuit_trips > 0)
    }

    /// Whether a failed setup should stop the start, or only be recorded.
    ///
    /// Hoisted out of `run_setups` because `optional` used to be honoured at
    /// exactly one of the three places a setup can fail. A setup that *exited*
    /// non-zero was tolerated; the same setup *hanging*, or running out of
    /// budget before its next retry, took the whole stack down -- the reverse of
    /// what `optional` means. `connector-catalog` is declared optional with a
    /// 600-second timeout precisely so an unreachable third-party catalog cannot
    /// stop a workspace, and a blackholed route defeated that.
    ///
    /// Returns the error to raise, or `None` when the caller should carry on to
    /// the next setup. A function rather than a closure because the caller has
    /// to `continue 'setups`, which a closure cannot do.
    fn optional_setup_outcome(setup: &HostSetupSpec, detail: String) -> Option<io::Error> {
        if setup.optional {
            // Logged rather than raised: the log line is the record, and the
            // stack still comes up.
            eprintln!("locald: {detail}");
            return None;
        }
        Some(io::Error::other(detail))
    }

    /// Where completed setup stamps live, beside the process ledger.
    ///
    /// Under the locald root on purpose: a local-data reset removes that whole
    /// directory, so a wiped database can never be left with a stamp claiming
    /// its migrations have already run.
    fn setup_stamp_path(&self) -> PathBuf {
        self.log_dir
            .parent()
            .unwrap_or(&self.log_dir)
            .join("setup-stamps.json")
    }

    /// Forget every completed setup, so the next start runs them all again.
    ///
    /// A local-data reset destroys the database the migrations stamp describes
    /// but leaves the locald root standing -- so without this the next start
    /// would skip migrations against an empty schema and the backend would come
    /// up against tables that do not exist. The full reinstall removes the root
    /// entirely and takes the stamps with it.
    pub fn forget_setup_stamps(&self) -> io::Result<()> {
        match fs::remove_file(self.setup_stamp_path()) {
            Err(error) if error.kind() != io::ErrorKind::NotFound => Err(error),
            _ => Ok(()),
        }
    }

    fn recorded_setup_stamps(&self) -> HashMap<String, String> {
        std::fs::read(self.setup_stamp_path())
            .ok()
            .and_then(|raw| serde_json::from_slice(&raw).ok())
            .unwrap_or_default()
    }

    /// Record a setup as done for this stamp. Written only after it succeeded.
    fn record_setup_stamp(&self, id: &str, stamp: &str) {
        let mut stamps = self.recorded_setup_stamps();
        stamps.insert(id.to_owned(), stamp.to_owned());
        // Best effort: a stamp that cannot be written costs the next start the
        // work again, which is exactly the behaviour before stamps existed.
        if let Ok(encoded) = serde_json::to_vec_pretty(&stamps) {
            let _ = write_private_atomic(&self.setup_stamp_path(), &encoded);
        }
    }

    fn run_setups(&self) -> io::Result<()> {
        let recorded = self.recorded_setup_stamps();
        'setups: for setup in &self.manifest.setup {
            if setup_is_already_done(setup.stamp.as_deref(), recorded.get(&setup.id)) {
                continue 'setups;
            }
            let mut environment = setup.env.clone();
            environment.extend(
                self.backend_environment
                    .lock()
                    .expect("backend environment lock poisoned")
                    .clone(),
            );
            environment.extend(self.service_environment("backend"));
            let deadline = Instant::now() + Duration::from_secs(setup.timeout_seconds);
            for attempt in 1..=setup.max_attempts {
                wait_for_setup_dependency(setup, &environment, deadline)?;
                let mut child = spawn_command(
                    &setup.command,
                    setup.cwd.as_deref(),
                    &environment,
                    process_log(&self.log_dir, &setup.id)?,
                )?;
                #[cfg(windows)]
                assign_child_to_windows_job(self.windows_job, &mut child)?;
                loop {
                    if let Some(status) = child.try_wait()? {
                        if status.success() {
                            // Only here. A stamp written anywhere else would
                            // let a failed or half-finished setup be skipped on
                            // the next start, which is worse than running it
                            // again.
                            if let Some(stamp) = setup.stamp.as_deref() {
                                self.record_setup_stamp(&setup.id, stamp);
                            }
                            continue 'setups;
                        }
                        if attempt == setup.max_attempts {
                            let detail = format!(
                                "{} setup exited with {status} after {attempt} attempts; see {}",
                                setup.id,
                                self.log_dir.join(format!("{}.log", setup.id)).display()
                            );
                            match Self::optional_setup_outcome(setup, detail) {
                                None => continue 'setups,
                                Some(error) => return Err(error),
                            }
                        }
                        let backoff = Duration::from_secs(
                            setup.retry_backoff_seconds.saturating_mul(attempt as u64),
                        );
                        if Instant::now() + backoff >= deadline {
                            let detail = format!(
                                "{} setup exited with {status}; see {}",
                                setup.id,
                                self.log_dir.join(format!("{}.log", setup.id)).display()
                            );
                            match Self::optional_setup_outcome(setup, detail) {
                                None => continue 'setups,
                                Some(error) => return Err(error),
                            }
                        }
                        writeln!(
                            process_log(&self.log_dir, &setup.id)?,
                            "lemma-locald: setup attempt {attempt} exited with {status}; retrying"
                        )?;
                        thread::sleep(backoff);
                        break;
                    }
                    if Instant::now() >= deadline {
                        // Terminate first, on both paths. An optional setup that
                        // hangs and is then tolerated would otherwise be left
                        // running as an orphan holding a copy of the backend
                        // environment -- Postgres and Redis passwords included.
                        let _ = terminate_process_group(&mut child);
                        let detail = format!(
                            "{} setup exceeded {} seconds; see {}",
                            setup.id,
                            setup.timeout_seconds,
                            self.log_dir.join(format!("{}.log", setup.id)).display()
                        );
                        match Self::optional_setup_outcome(setup, detail) {
                            None => continue 'setups,
                            Some(error) => {
                                return Err(io::Error::new(io::ErrorKind::TimedOut, error))
                            }
                        }
                    }
                    thread::sleep(Duration::from_millis(50));
                }
            }
        }
        Ok(())
    }

    pub fn stop_all(&self) -> io::Result<()> {
        self.health_ready.store(false, Ordering::Release);
        self.desired_running.store(false, Ordering::Release);
        let mut first_error = None;
        for id in self.ordered_ids.iter().rev() {
            if let Err(error) = self.stop_process(id) {
                first_error.get_or_insert(error);
            }
        }
        if first_error.is_none() {
            if let Err(error) = self.reserve_idle_ports() {
                first_error = Some(error);
            }
        }
        if let Some(error) = first_error {
            Err(error)
        } else {
            Ok(())
        }
    }

    pub fn restart_all(&self) -> io::Result<()> {
        self.stop_all()?;
        self.start_all()
    }

    pub fn restart_backend(&self) -> io::Result<()> {
        self.health_ready.store(false, Ordering::Release);
        self.desired_running.store(false, Ordering::Release);
        self.stop_process("backend")?;
        {
            let mut state = self.state.lock().expect("host process lock poisoned");
            state.circuit_open.remove("backend");
            state.circuit_trips.remove("backend");
            state.restart_history.remove("backend");
            state.restart_not_before.remove("backend");
        }
        self.spawn_if_missing("backend")?;
        if let Some(health) = self.health_spec("backend") {
            if let Err(error) = self.wait_process_health("backend", &health) {
                let _ = self.stop_process("backend");
                return Err(io::Error::other(format!(
                    "backend failed health gate after configuration: {error}"
                )));
            }
        }
        self.health_ready.store(true, Ordering::Release);
        self.desired_running.store(true, Ordering::Release);
        Ok(())
    }

    pub fn status(&self) -> Vec<HostProcessStatus> {
        self.inspect_exits();
        let state = self.state.lock().expect("host process lock poisoned");
        self.ordered_ids
            .iter()
            .map(|id| HostProcessStatus {
                id: id.clone(),
                running: state.children.contains_key(id),
                pid: state.children.get(id).map(|child| child.child.id()),
                circuit_open: state.circuit_open.contains(id),
                circuit_trips: state.circuit_trips.get(id).copied().unwrap_or(0),
                restart_count: state
                    .restart_history
                    .get(id)
                    .map(VecDeque::len)
                    .unwrap_or(0),
                last_exit: state.last_exit.get(id).cloned(),
            })
            .collect()
    }

    pub fn status_event(&self, id: Option<&Value>) -> Value {
        let components = self.status();
        let all_running = components.iter().all(|component| component.running);
        let dependency_ready = self.dependency_ready.load(Ordering::Acquire);
        let dependency_error = self
            .dependency_error
            .lock()
            .expect("dependency error lock poisoned")
            .clone();
        let ready = all_running
            && self.health_ready.load(Ordering::Acquire)
            && !self.startup_in_progress.load(Ordering::Acquire)
            && dependency_ready;
        let desired = self.desired_running();
        let failed =
            Self::components_report_failure(&components) || (desired && dependency_error.is_some());
        let mut event = json!({
            "v": 1,
            "event": "status",
            "mode": "host-packs",
            "release": self.release(),
            "status": if ready {
                "running"
            } else if failed {
                "error"
            } else if desired {
                "starting"
            } else {
                "stopped"
            },
            "ready": ready,
            "running": ready || (desired && components.iter().any(|process| process.running)),
            "components": components,
            "capabilities": self.capabilities(),
            "dependency_ready": dependency_ready,
            "dependency_error": dependency_error,
            "runtime_generation": self
                .runtime_generation
                .lock()
                .expect("runtime generation lock poisoned")
                .clone(),
        });
        if let Some(id) = id {
            event["id"] = id.clone();
        }
        event
    }

    pub fn capabilities(&self) -> Option<Value> {
        self.capabilities_result().ok()
    }

    fn capabilities_result(&self) -> io::Result<Value> {
        if !self.health_ready.load(Ordering::Acquire) {
            return Err(io::Error::new(
                io::ErrorKind::NotConnected,
                "backend core health is not ready",
            ));
        }
        let mut health = self
            .health_spec("backend")
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "backend health is missing"))?;
        health.url = health
            .url
            .strip_suffix("/health/ready")
            .map(|base| format!("{base}/health/capabilities"))
            .ok_or_else(|| {
                io::Error::new(
                    io::ErrorKind::InvalidInput,
                    "backend health URL has an unexpected path",
                )
            })?;
        let body = probe_http(&health)?;
        serde_json::from_str(&body).map_err(io::Error::other)
    }

    fn spawn_if_missing(&self, id: &str) -> io::Result<()> {
        {
            let mut state = self.state.lock().expect("host process lock poisoned");
            if let Some(child) = state.children.get_mut(id) {
                if child.child.try_wait()?.is_none() {
                    return Ok(());
                }
            }
            state.children.remove(id);
        }

        let spec = self.process_spec_for_spawn(id)?;
        let mut child = spawn_process(&spec, &self.log_dir)?;
        #[cfg(windows)]
        assign_child_to_windows_job(self.windows_job, &mut child)?;
        if let Err(error) = self.record_child(id, &mut child) {
            if let Some(status) = child.try_wait()? {
                let excerpt = tail_log(&self.log_dir.join(format!("{id}.log")), 8 * 1024);
                let suffix = excerpt
                    .filter(|value| !value.trim().is_empty())
                    .map(|value| format!("; recent log:\n{}", self.redact_excerpt(value)))
                    .unwrap_or_default();
                return Err(io::Error::other(format!(
                    "{id} process exited with {status}{suffix}"
                )));
            }
            let _ = terminate_process_group(&mut child);
            return Err(io::Error::other(format!(
                "could not record ownership of {id}: {error}"
            )));
        }
        self.state
            .lock()
            .expect("host process lock poisoned")
            .children
            .insert(
                id.to_owned(),
                ManagedChild {
                    child,
                    started_at: Instant::now(),
                },
            );
        Ok(())
    }

    fn process_spec_for_spawn(&self, id: &str) -> io::Result<HostProcessSpec> {
        let mut spec = self
            .by_id
            .get(id)
            .cloned()
            .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, id.to_owned()))?;
        if id == "backend" {
            spec.env.extend(
                self.backend_environment
                    .lock()
                    .expect("backend environment lock poisoned")
                    .clone(),
            );
        }
        spec.env.extend(self.service_environment(id));
        let generation = self
            .runtime_generation
            .lock()
            .expect("runtime generation lock poisoned")
            .clone();
        if !generation.is_empty() {
            match id {
                "backend" => {
                    spec.env
                        .insert("LEMMA_RUNTIME_INSTANCE_ID".into(), generation.clone());
                }
                "frontend" => {
                    spec.env.insert(
                        "NEXT_PUBLIC_LEMMA_RUNTIME_INSTANCE_ID".into(),
                        generation.clone(),
                    );
                }
                _ => {}
            }
            if let Some(health) = spec.health.as_mut() {
                health.expected_body = Some(generation);
            }
        }
        Ok(spec)
    }

    fn health_spec(&self, id: &str) -> Option<HttpHealthSpec> {
        let mut health = self.by_id.get(id)?.health.clone()?;
        let generation = self
            .runtime_generation
            .lock()
            .expect("runtime generation lock poisoned")
            .clone();
        if !generation.is_empty() {
            health.expected_body = Some(generation);
        }
        Some(health)
    }

    fn stop_process(&self, id: &str) -> io::Result<()> {
        let child = self
            .state
            .lock()
            .expect("host process lock poisoned")
            .children
            .remove(id);
        let result = match child {
            Some(mut child) => terminate_process_group(&mut child.child),
            None => Ok(()),
        };
        if result.is_ok() {
            self.remove_ledger_entry(id)?;
        }
        result
    }

    fn release_idle_port_for(&self, id: &str) {
        let Some(port) = self.managed_service_port(id) else {
            return;
        };
        self.idle_port_reservations
            .lock()
            .expect("idle port reservation lock poisoned")
            .remove(&port);
    }

    fn reserve_idle_ports(&self) -> io::Result<()> {
        let Some(runtime) = self.manifest.managed_runtime.as_ref() else {
            return Ok(());
        };
        let mut reservations = self
            .idle_port_reservations
            .lock()
            .expect("idle port reservation lock poisoned");
        if let std::collections::hash_map::Entry::Vacant(entry) =
            reservations.entry(runtime.ports.backend)
        {
            let listener = bind_idle_port(runtime.ports.backend)?;
            entry.insert(listener);
        }
        if let std::collections::hash_map::Entry::Vacant(entry) =
            reservations.entry(runtime.ports.frontend)
        {
            let listener = bind_idle_port(runtime.ports.frontend)?;
            entry.insert(listener);
        }
        Ok(())
    }

    fn managed_service_port(&self, id: &str) -> Option<u16> {
        let ports = &self.manifest.managed_runtime.as_ref()?.ports;
        match id {
            "backend" => Some(ports.backend),
            "frontend" => Some(ports.frontend),
            _ => None,
        }
    }

    fn inspect_exits(&self) {
        let mut state = self.state.lock().expect("host process lock poisoned");
        let mut exited = Vec::new();
        for (id, managed) in &mut state.children {
            match managed.child.try_wait() {
                Ok(Some(status)) => exited.push((
                    id.clone(),
                    format!(
                        "{status} after {} ms",
                        managed.started_at.elapsed().as_millis()
                    ),
                )),
                Err(error) => exited.push((id.clone(), format!("inspection failed: {error}"))),
                Ok(None) => {}
            }
        }
        if !exited.is_empty() {
            self.health_ready.store(false, Ordering::Release);
        }
        for (id, exit) in exited {
            state.children.remove(&id);
            state.last_exit.insert(id.clone(), exit);
            let _ = self.remove_ledger_entry(&id);
        }
    }

    /// Hold each service's log to its ceiling while the service is running.
    ///
    /// `process_log` rotates when it opens the file, which is once, at spawn.
    /// The handle it returns then *is* the child's stdout and stderr for the
    /// whole life of the process -- so a backend that stays up for days was
    /// never checked again. One install was found with a 16 MB `backend.log`
    /// still growing at 8 MB an hour, and the only thing that would ever have
    /// truncated it was a restart.
    ///
    /// Rotating a live log is exactly what `rotate_log` was built for: it
    /// copies aside and truncates in place rather than renaming, so the writer
    /// holding the descriptor keeps writing to the same file and simply finds
    /// it back at zero. That property was already relied on at spawn; nothing
    /// was calling it afterwards.
    fn rotate_service_logs(&self) {
        for id in &self.ordered_ids {
            let path = self.log_dir.join(format!("{id}.log"));
            // Best effort, per service. A log that cannot be rotated -- removed
            // out from under us, or briefly locked on Windows -- must not stop
            // the others being rotated, and must not take down the supervisor
            // thread that is also the crash monitor.
            let _ = rotate_log(&path, SERVICE_LOG_MAX_BYTES);
        }
    }

    fn reconcile_crashes(&self) {
        self.inspect_exits();
        if !self.desired_running.load(Ordering::Acquire)
            || self.startup_in_progress.load(Ordering::Acquire)
        {
            return;
        }

        for id in &self.ordered_ids {
            let spec = &self.by_id[id];
            let ready_to_spawn = {
                let mut state = self.state.lock().expect("host process lock poisoned");
                let now = Instant::now();
                let window = Duration::from_secs(spec.restart.window_seconds);
                // Prune *before* consulting the circuit, not after. The old
                // order tested `circuit_open` first, so once a service tripped,
                // its history was never trimmed again and no amount of elapsed
                // time could reopen it -- a single transient burst (a laptop
                // waking with the VM's forwarders not yet up) condemned the
                // service until the user restarted the whole app, and nothing on
                // screen said that was the remedy.
                //
                // Closing needs a full quiet window, so this is a real cooldown
                // rather than an unconditional reset.
                {
                    let history = state.restart_history.entry(id.clone()).or_default();
                    while history
                        .front()
                        .is_some_and(|started| now.duration_since(*started) > window)
                    {
                        history.pop_front();
                    }
                    if history.len() < spec.restart.max_restarts {
                        state.circuit_open.remove(id);
                    }
                }
                if state.children.contains_key(id)
                    || state.circuit_open.contains(id)
                    || spec
                        .dependencies
                        .iter()
                        .any(|dependency| !state.children.contains_key(dependency))
                {
                    false
                } else if let Some(deadline) = state.restart_not_before.get(id) {
                    *deadline <= now
                } else {
                    // Length first, so the borrow of `restart_history` is over
                    // before `circuit_open` and `circuit_trips` are touched.
                    let attempts = state.restart_history.entry(id.clone()).or_default().len();
                    if attempts >= spec.restart.max_restarts {
                        state.circuit_open.insert(id.clone());
                        *state.circuit_trips.entry(id.clone()).or_insert(0) += 1;
                    } else {
                        state
                            .restart_history
                            .entry(id.clone())
                            .or_default()
                            .push_back(now);
                        state.restart_not_before.insert(
                            id.clone(),
                            now + Duration::from_secs(spec.restart.backoff_seconds),
                        );
                    }
                    false
                }
            };
            if ready_to_spawn {
                let result = self.spawn_if_missing(id);
                let result = result.and_then(|_| {
                    if let Some(health) = self.health_spec(id) {
                        if let Err(error) = self.wait_process_health(id, &health) {
                            let _ = self.stop_process(id);
                            return Err(error);
                        }
                    }
                    Ok(())
                });
                let mut state = self.state.lock().expect("host process lock poisoned");
                state.restart_not_before.remove(id);
                if let Err(error) = result {
                    state
                        .last_exit
                        .insert(id.clone(), format!("restart failed: {error}"));
                } else if state.children.len() == self.ordered_ids.len()
                    && self.dependency_ready.load(Ordering::Acquire)
                {
                    self.health_ready.store(true, Ordering::Release);
                }
            }
        }
    }

    fn wait_process_health(&self, id: &str, spec: &HttpHealthSpec) -> io::Result<()> {
        let deadline = Instant::now() + Duration::from_secs(spec.timeout_seconds);
        let stabilization = Duration::from_secs(spec.stabilization_seconds);
        let mut healthy_since = None;
        let mut last_error = None;
        while Instant::now() < deadline {
            if let Some(exit) = self.take_process_exit(id)? {
                let excerpt = tail_log(&self.log_dir.join(format!("{id}.log")), 8 * 1024);
                let suffix = excerpt
                    .filter(|value| !value.trim().is_empty())
                    .map(|value| format!("; recent log:\n{}", self.redact_excerpt(value)))
                    .unwrap_or_default();
                return Err(io::Error::other(format!(
                    "process exited with {exit}{suffix}"
                )));
            }
            match probe_http(spec) {
                Ok(_) => {
                    let since = healthy_since.get_or_insert_with(Instant::now);
                    if since.elapsed() >= stabilization {
                        return Ok(());
                    }
                }
                Err(error) => {
                    healthy_since = None;
                    last_error = Some(error);
                }
            }
            thread::sleep(Duration::from_millis(250));
        }
        Err(last_error.unwrap_or_else(|| io::Error::new(io::ErrorKind::TimedOut, "health timeout")))
    }

    fn take_process_exit(&self, id: &str) -> io::Result<Option<String>> {
        let mut state = self.state.lock().expect("host process lock poisoned");
        let Some(managed) = state.children.get_mut(id) else {
            return Ok(Some("process is not running".into()));
        };
        let Some(status) = managed.child.try_wait()? else {
            return Ok(None);
        };
        let exit = format!(
            "{status} after {} ms",
            managed.started_at.elapsed().as_millis()
        );
        state.children.remove(id);
        state.last_exit.insert(id.to_owned(), exit.clone());
        self.health_ready.store(false, Ordering::Release);
        Ok(Some(exit))
    }

    fn redact_excerpt(&self, mut excerpt: String) -> String {
        let mut secrets = Vec::new();
        if let Some(runtime) = self.manifest.managed_runtime.as_ref() {
            secrets.push(runtime.credentials.postgres_password.as_str());
            secrets.push(runtime.credentials.redis_password.as_str());
        }
        for spec in &self.manifest.services {
            for (key, value) in &spec.env {
                if sensitive_key(key) && value.len() >= 8 {
                    secrets.push(value);
                }
            }
        }
        let backend = self
            .backend_environment
            .lock()
            .expect("backend environment lock poisoned");
        for (key, value) in backend.iter() {
            if sensitive_key(key) && value.len() >= 8 {
                secrets.push(value);
            }
        }
        secrets.sort_by_key(|value| std::cmp::Reverse(value.len()));
        secrets.dedup();
        for secret in secrets {
            excerpt = excerpt.replace(secret, "[redacted]");
        }
        excerpt
    }

    fn record_child(&self, id: &str, child: &mut Child) -> io::Result<()> {
        let _guard = self
            .process_ledger_lock
            .lock()
            .expect("process ledger lock poisoned");
        let identity = settled_process_identity(child)?;
        let generation = self
            .runtime_generation
            .lock()
            .expect("runtime generation lock poisoned")
            .clone();
        let mut ledger = read_process_ledger(&self.process_ledger_path)
            .filter(|ledger| ledger.installation_id == self.installation_id)
            .unwrap_or_else(|| ProcessLedger {
                schema_version: PROCESS_LEDGER_SCHEMA_VERSION,
                installation_id: self.installation_id.clone(),
                entries: Vec::new(),
            });
        ledger.entries.retain(|entry| entry.service_id != id);
        ledger.entries.push(ProcessLedgerEntry {
            service_id: id.to_owned(),
            pid: child.id(),
            executable: identity.executable,
            start_identity: identity.start_identity,
            installation_id: self.installation_id.clone(),
            runtime_generation: generation,
        });
        write_process_ledger(&self.process_ledger_path, &ledger)
    }

    fn remove_ledger_entry(&self, id: &str) -> io::Result<()> {
        let _guard = self
            .process_ledger_lock
            .lock()
            .expect("process ledger lock poisoned");
        let Some(mut ledger) = read_process_ledger(&self.process_ledger_path) else {
            return Ok(());
        };
        if ledger.installation_id != self.installation_id {
            return Ok(());
        }
        ledger.entries.retain(|entry| entry.service_id != id);
        write_process_ledger(&self.process_ledger_path, &ledger)
    }
}

fn loopback_http_port(url: &str) -> Option<u16> {
    let authority = url
        .strip_prefix("http://127.0.0.1:")
        .or_else(|| url.strip_prefix("http://localhost:"))?
        .split(['/', '?', '#'])
        .next()?;
    authority.parse().ok()
}

pub(crate) fn reclaim_persisted_installation_processes(state_root: &Path) -> io::Result<()> {
    let manifest_path = state_root.join("host-pack.json");
    let raw = match fs::read_to_string(&manifest_path) {
        Ok(raw) => raw,
        Err(error) if error.kind() == io::ErrorKind::NotFound => return Ok(()),
        Err(error) => return Err(error),
    };
    // A damaged prior manifest is not sufficient proof of ownership. Leave
    // every process untouched; dynamic port allocation will safely route
    // around any listener that remains.
    let Ok(manifest) = serde_json::from_str::<HostPackManifest>(&raw) else {
        return Ok(());
    };
    let installation_id = load_or_create_installation_id(state_root)?;
    reclaim_verified_processes(
        &state_root.join("processes.json"),
        &installation_id,
        &manifest,
    )
}

fn load_or_create_installation_id(root: &Path) -> io::Result<String> {
    let path = root.join("installation.id");
    if let Ok(value) = fs::read_to_string(&path) {
        let value = value.trim();
        if value.len() == 32 && value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
            return Ok(value.to_owned());
        }
    }
    let value = random_generation()?;
    write_private_atomic(&path, format!("{value}\n").as_bytes())?;
    Ok(value)
}

pub(crate) fn installation_identity(root: &Path) -> io::Result<String> {
    load_or_create_installation_id(root)
}

fn read_process_ledger(path: &Path) -> Option<ProcessLedger> {
    let raw = fs::read(path).ok()?;
    if raw.len() > 1024 * 1024 {
        return None;
    }
    let ledger = serde_json::from_slice::<ProcessLedger>(&raw).ok()?;
    (ledger.schema_version == PROCESS_LEDGER_SCHEMA_VERSION).then_some(ledger)
}

fn write_process_ledger(path: &Path, ledger: &ProcessLedger) -> io::Result<()> {
    write_private_atomic(path, &serde_json::to_vec_pretty(ledger)?)
}

fn write_private_atomic(path: &Path, contents: &[u8]) -> io::Result<()> {
    let parent = path
        .parent()
        .ok_or_else(|| io::Error::other("process ledger has no parent"))?;
    fs::create_dir_all(parent)?;
    let temporary = parent.join(format!(
        ".processes-{}-{}.tmp",
        std::process::id(),
        random_generation()?
    ));
    let _ = fs::remove_file(&temporary);
    let mut options = OpenOptions::new();
    options.write(true).create_new(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    let mut file = options.open(&temporary)?;
    file.write_all(contents)?;
    file.sync_all()?;
    replace_private_file(&temporary, path)?;
    #[cfg(unix)]
    File::open(parent)?.sync_all()?;
    Ok(())
}

fn replace_private_file(source: &Path, destination: &Path) -> io::Result<()> {
    // No delete-then-rename on Windows. `fs::rename` is MoveFileExW with
    // MOVEFILE_REPLACE_EXISTING and already replaces the destination, so the
    // unlink bought nothing and cost two things: a window in which the process
    // ledger simply did not exist -- crash there and the previous backend and
    // frontend are never reclaimed, and keep their ports -- and a second way to
    // fail, since removing a file anything has open (a virus scanner, moments
    // after it was written) is a sharing violation.
    fs::rename(source, destination)
}

fn reclaim_verified_processes(
    ledger_path: &Path,
    installation_id: &str,
    manifest: &HostPackManifest,
) -> io::Result<()> {
    let Some(ledger) = read_process_ledger(ledger_path) else {
        return Ok(());
    };
    if ledger.installation_id != installation_id {
        return Ok(());
    }
    for entry in &ledger.entries {
        if entry.installation_id != installation_id
            || entry.runtime_generation.len() != 32
            || !entry
                .runtime_generation
                .bytes()
                .all(|byte| byte.is_ascii_hexdigit())
        {
            continue;
        }
        let Some(spec) = manifest
            .services
            .iter()
            .find(|spec| spec.id == entry.service_id)
        else {
            continue;
        };
        let Some(expected) = spec
            .command
            .first()
            .and_then(|path| Path::new(path).canonicalize().ok())
        else {
            continue;
        };
        let Ok(identity) = process_identity(entry.pid) else {
            continue;
        };
        if identity.executable == entry.executable
            && identity.start_identity == entry.start_identity
            && Path::new(&identity.executable)
                .canonicalize()
                .is_ok_and(|actual| actual == expected)
        {
            terminate_verified_process(entry.pid)?;
        }
    }
    write_process_ledger(
        ledger_path,
        &ProcessLedger {
            schema_version: PROCESS_LEDGER_SCHEMA_VERSION,
            installation_id: installation_id.to_owned(),
            entries: Vec::new(),
        },
    )
}

/// How long `process_identity` waits for a freshly forked process to finish
/// `exec` before giving up on naming its executable.
#[cfg(unix)]
const IDENTITY_SETTLE_ATTEMPTS: u32 = 20;
#[cfg(unix)]
const IDENTITY_SETTLE_INTERVAL: Duration = Duration::from_millis(25);
/// The backstop for `settled_process_identity`, which normally stops on one of
/// the two things it is actually watching for rather than on the clock.
#[cfg(unix)]
const IDENTITY_SETTLE_TIMEOUT: Duration = Duration::from_secs(5);

/// One `ps` query. `Ok(None)` means the process exists but has not yet reported
/// a usable executable name, so the caller should look again.
///
/// macOS and the BSDs only. `ps -o comm=` prints an absolute path there, which
/// is what makes this work; on Linux the same flag prints a bare command name
/// truncated to fifteen characters, so every `canonicalize` below failed and
/// every process this daemon spawned was reported as unidentifiable. Linux has
/// its own implementation, and a better one -- see the `/proc` version below.
#[cfg(all(unix, not(target_os = "linux")))]
fn query_process_identity(pid: &str) -> io::Result<Option<ProcessIdentity>> {
    let executable = Command::new("/bin/ps")
        .args(["-p", pid, "-o", "comm="])
        .output()?;
    let started = Command::new("/bin/ps")
        .args(["-p", pid, "-o", "lstart="])
        .output()?;
    if !executable.status.success() || !started.status.success() {
        return Err(io::Error::new(io::ErrorKind::NotFound, "process not found"));
    }
    let executable = String::from_utf8(executable.stdout)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    let executable = executable.trim();
    // `ps` brackets the name as `(sh)` while a process has forked but not yet
    // finished `exec`, and while it is exiting. That placeholder is not a path,
    // so canonicalizing it fails with ENOENT — which used to make a child that
    // was merely still starting look unidentifiable, and cost it the ownership
    // record it had just been spawned to receive.
    if executable.starts_with('(') && executable.ends_with(')') {
        return Ok(None);
    }
    // The bracket is not the only shape that transient takes. `sh -c "…"`
    // execs into the command it was given, and a name read across that
    // boundary can be unbracketed and still name nothing that resolves. That
    // is the same "not settled yet" the bracket means, so it retries too —
    // propagating ENOENT here spent none of the 500ms settle budget and
    // reported a starting child as one whose ownership could not be recorded.
    // A child that never settles still fails, with the outer error that says
    // so rather than a bare "No such file or directory".
    let Ok(executable) = Path::new(executable).canonicalize() else {
        return Ok(None);
    };
    let executable = executable.to_string_lossy().into_owned();
    let start_identity = String::from_utf8(started.stdout)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?
        .trim()
        .to_owned();
    if start_identity.is_empty() {
        return Err(io::Error::other("process start identity was empty"));
    }
    Ok(Some(ProcessIdentity {
        executable,
        start_identity,
    }))
}

/// The same question, asked of `/proc` rather than of `ps`.
///
/// Linux does not answer `ps -o comm=` with a path, so the BSD implementation
/// above could never identify anything here: `Path::new("sleep").canonicalize()`
/// fails, every sample looked like a process that had not settled, and the
/// ownership ledger -- the thing that guarantees this daemon only ever signals
/// processes it started -- recorded nothing at all.
///
/// `/proc/<pid>/exe` is a better source than `ps` in both directions. It is the
/// kernel's own answer, already absolute and already resolved through symlinks,
/// and it is readable only for a process of the same user, which is a check
/// worth having for free. `starttime` from `/proc/<pid>/stat` is likewise a
/// stronger identity than `ps lstart`, whose one-second granularity is exactly
/// the window in which a recycled PID looks like the process it replaced.
///
/// One deliberate difference from macOS. `ps` reports `(sh)` for a process that
/// has forked but not yet finished `exec`, which is the placeholder the settle
/// loop exists to wait out. `/proc/<pid>/exe` has no such state -- during that
/// window it simply names the binary the process is still running. So on Linux
/// `Ok(None)` means only "gone or not ours", and a record written mid-exec
/// names the pre-exec binary. That record then fails to match later, and a
/// ledger entry that fails to match is one this daemon declines to signal --
/// the safe direction, and the same one an unreadable link takes.
#[cfg(target_os = "linux")]
fn query_process_identity(pid: &str) -> io::Result<Option<ProcessIdentity>> {
    let executable = match std::fs::read_link(format!("/proc/{pid}/exe")) {
        Ok(path) => path,
        // ESRCH once the process is gone, EACCES for one we do not own, ENOENT
        // for a kernel thread. None of the three is a process this daemon may
        // claim, and none becomes one by looking again.
        Err(error) => return Err(io::Error::new(io::ErrorKind::NotFound, error)),
    };
    let stat = std::fs::read_to_string(format!("/proc/{pid}/stat"))
        .map_err(|error| io::Error::new(io::ErrorKind::NotFound, error))?;
    let start_identity = process_start_time(&stat)
        .ok_or_else(|| io::Error::other("process start identity was empty"))?;
    Ok(Some(ProcessIdentity {
        executable: executable.to_string_lossy().into_owned(),
        start_identity,
    }))
}

/// Field 22 of `/proc/<pid>/stat`: when the process started, in clock ticks.
///
/// Split from the read so it can be tested on any platform, and because the
/// parse has one trap in it. Field 2 is the command name in parentheses and may
/// itself contain spaces *and* parentheses -- a process is free to call itself
/// `my (weird) name` -- so splitting the line on whitespace mis-numbers every
/// field after it. Everything before the final `)` has to go first.
#[cfg(any(target_os = "linux", test))]
fn process_start_time(stat: &str) -> Option<String> {
    let after_comm = &stat[stat.rfind(')')? + 1..];
    // The first field after the comm is `state`, which is number 3.
    after_comm
        .split_whitespace()
        .nth(22 - 3)
        .filter(|ticks| !ticks.is_empty())
        .map(str::to_owned)
}

#[cfg(unix)]
pub(crate) fn process_identity(pid: u32) -> io::Result<ProcessIdentity> {
    let pid = pid.to_string();
    for attempt in 0..IDENTITY_SETTLE_ATTEMPTS {
        if let Some(identity) = query_process_identity(&pid)? {
            return Ok(identity);
        }
        if attempt + 1 < IDENTITY_SETTLE_ATTEMPTS {
            thread::sleep(IDENTITY_SETTLE_INTERVAL);
        }
    }
    Err(io::Error::new(
        io::ErrorKind::NotFound,
        "process never reported an executable path",
    ))
}

/// The identity of a child this daemon just spawned, waited for rather than
/// sampled a fixed number of times.
///
/// Two things end the window in which a fresh child has no path to report: it
/// finishes `exec`, or it exits. Both are observable, so both are what this
/// waits on. Counting samples instead meant a child that exited immediately
/// still cost the whole settle budget before reporting the one thing worth
/// knowing about it — that it had already died, and with what status.
#[cfg(unix)]
fn settled_process_identity(child: &mut Child) -> io::Result<ProcessIdentity> {
    let pid = child.id().to_string();
    let deadline = Instant::now() + IDENTITY_SETTLE_TIMEOUT;
    loop {
        if let Some(identity) = query_process_identity(&pid)? {
            return Ok(identity);
        }
        if child.try_wait()?.is_some() {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "process exited before reporting an executable path",
            ));
        }
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::NotFound,
                "process never reported an executable path",
            ));
        }
        thread::sleep(IDENTITY_SETTLE_INTERVAL);
    }
}

/// Windows names a process's image at creation, so there is no window to wait
/// out and nothing a live child can report that a query would miss.
#[cfg(windows)]
fn settled_process_identity(child: &mut Child) -> io::Result<ProcessIdentity> {
    process_identity(child.id())
}

#[cfg(unix)]
pub(crate) fn terminate_verified_process(pid: u32) -> io::Result<()> {
    let pid = i32::try_from(pid).map_err(|_| io::Error::other("invalid process id"))?;
    // SAFETY: the caller has matched installation, executable and OS start identity.
    let result = unsafe { libc::kill(pid, libc::SIGTERM) };
    if result != 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() == Some(libc::ESRCH) {
            return Ok(());
        }
        return Err(error);
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        // SAFETY: signal zero only checks whether this exact PID still exists.
        if unsafe { libc::kill(pid, 0) } != 0 {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    // SAFETY: identity was checked immediately before termination.
    unsafe { libc::kill(pid, libc::SIGKILL) };
    Ok(())
}

#[cfg(windows)]
pub(crate) fn process_identity(pid: u32) -> io::Result<ProcessIdentity> {
    use windows_sys::Win32::Foundation::{CloseHandle, FILETIME};
    use windows_sys::Win32::System::Threading::{
        GetProcessTimes, OpenProcess, QueryFullProcessImageNameW, PROCESS_QUERY_LIMITED_INFORMATION,
    };

    let handle = unsafe { OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, 0, pid) };
    if handle.is_null() {
        return Err(io::Error::last_os_error());
    }
    let result = (|| {
        let mut path = vec![0_u16; 32_768];
        let mut path_len = path.len() as u32;
        if unsafe { QueryFullProcessImageNameW(handle, 0, path.as_mut_ptr(), &mut path_len) } == 0 {
            return Err(io::Error::last_os_error());
        }
        let executable = PathBuf::from(String::from_utf16_lossy(&path[..path_len as usize]))
            .canonicalize()?
            .to_string_lossy()
            .into_owned();
        let mut creation: FILETIME = unsafe { std::mem::zeroed() };
        let mut exit: FILETIME = unsafe { std::mem::zeroed() };
        let mut kernel: FILETIME = unsafe { std::mem::zeroed() };
        let mut user: FILETIME = unsafe { std::mem::zeroed() };
        if unsafe { GetProcessTimes(handle, &mut creation, &mut exit, &mut kernel, &mut user) } == 0
        {
            return Err(io::Error::last_os_error());
        }
        Ok(ProcessIdentity {
            executable,
            start_identity: format!(
                "{:08x}{:08x}",
                creation.dwHighDateTime, creation.dwLowDateTime
            ),
        })
    })();
    unsafe { CloseHandle(handle) };
    result
}

#[cfg(windows)]
pub(crate) fn terminate_verified_process(pid: u32) -> io::Result<()> {
    use windows_sys::Win32::Foundation::CloseHandle;
    use windows_sys::Win32::System::Threading::{
        OpenProcess, TerminateProcess, PROCESS_QUERY_LIMITED_INFORMATION, PROCESS_TERMINATE,
    };
    let handle = unsafe {
        OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION | PROCESS_TERMINATE,
            0,
            pid,
        )
    };
    if handle.is_null() {
        return Err(io::Error::last_os_error());
    }
    let result = if unsafe { TerminateProcess(handle, 1) } == 0 {
        Err(io::Error::last_os_error())
    } else {
        Ok(())
    };
    unsafe { CloseHandle(handle) };
    result
}

fn sensitive_key(key: &str) -> bool {
    let key = key.to_ascii_lowercase();
    ["password", "secret", "token", "api_key", "apikey"]
        .iter()
        .any(|marker| key.contains(marker))
}

fn random_generation() -> io::Result<String> {
    let mut bytes = [0_u8; 16];
    getrandom::fill(&mut bytes)
        .map_err(|error| io::Error::other(format!("runtime generation failed: {error}")))?;
    Ok(bytes.iter().map(|byte| format!("{byte:02x}")).collect())
}

fn reserve_managed_app_ports(manifest: &HostPackManifest) -> io::Result<HashMap<u16, TcpListener>> {
    let Some(runtime) = manifest.managed_runtime.as_ref() else {
        return Ok(HashMap::new());
    };
    let mut reservations = HashMap::new();
    for port in [runtime.ports.backend, runtime.ports.frontend] {
        reservations.insert(port, bind_idle_port(port)?);
    }
    Ok(reservations)
}

fn bind_idle_port(port: u16) -> io::Result<TcpListener> {
    TcpListener::bind((Ipv4Addr::LOCALHOST, port)).map_err(|error| {
        io::Error::new(
            error.kind(),
            format!("could not reserve Lemma's local port {port}: {error}"),
        )
    })
}

fn validate_and_order(manifest: &HostPackManifest) -> io::Result<Vec<String>> {
    if manifest.schema_version != MANIFEST_SCHEMA_VERSION {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            format!("unsupported host-pack schema {}", manifest.schema_version),
        ));
    }
    if manifest.release.trim().is_empty() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "host-pack release is empty",
        ));
    }

    let mut specs = HashMap::new();
    for spec in &manifest.services {
        if !valid_id(&spec.id) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("invalid host service id {:?}", spec.id),
            ));
        }
        if spec.command.is_empty() || spec.command[0].is_empty() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("{} has no command", spec.id),
            ));
        }
        if specs.insert(spec.id.as_str(), spec).is_some() {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("duplicate host service {}", spec.id),
            ));
        }
    }
    for required in REQUIRED_SERVICES {
        if !specs.contains_key(required) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("host-pack manifest is missing {required}"),
            ));
        }
    }
    let mut setups = HashSet::new();
    for setup in &manifest.setup {
        if !valid_id(&setup.id)
            || setup.command.is_empty()
            || setup.command[0].is_empty()
            || setup.timeout_seconds == 0
            || !(1..=5).contains(&setup.max_attempts)
            || setup.retry_backoff_seconds > 60
        {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("invalid host setup {}", setup.id),
            ));
        }
        if !setups.insert(setup.id.as_str()) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("duplicate host setup {}", setup.id),
            ));
        }
    }
    for required in REQUIRED_SETUPS {
        if !setups.contains(required) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("host-pack manifest is missing {required} setup"),
            ));
        }
    }
    for spec in &manifest.services {
        for dependency in &spec.dependencies {
            if !specs.contains_key(dependency.as_str()) {
                return Err(io::Error::new(
                    io::ErrorKind::InvalidData,
                    format!("{} depends on unknown service {dependency}", spec.id),
                ));
            }
        }
    }

    fn visit<'a>(
        id: &'a str,
        specs: &HashMap<&'a str, &'a HostProcessSpec>,
        visiting: &mut HashSet<&'a str>,
        visited: &mut HashSet<&'a str>,
        ordered: &mut Vec<String>,
    ) -> io::Result<()> {
        if visited.contains(id) {
            return Ok(());
        }
        if !visiting.insert(id) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                format!("host service dependency cycle includes {id}"),
            ));
        }
        for dependency in &specs[id].dependencies {
            visit(dependency, specs, visiting, visited, ordered)?;
        }
        visiting.remove(id);
        visited.insert(id);
        ordered.push(id.to_owned());
        Ok(())
    }

    let mut ordered = Vec::new();
    let mut visiting = HashSet::new();
    let mut visited = HashSet::new();
    for id in specs.keys() {
        visit(id, &specs, &mut visiting, &mut visited, &mut ordered)?;
    }
    Ok(ordered)
}

fn valid_id(id: &str) -> bool {
    !id.is_empty()
        && id.len() <= 32
        && id
            .bytes()
            .all(|byte| byte.is_ascii_lowercase() || byte.is_ascii_digit() || byte == b'-')
}

fn spawn_process(spec: &HostProcessSpec, log_dir: &Path) -> io::Result<Child> {
    spawn_command(
        &spec.command,
        spec.cwd.as_deref(),
        &spec.env,
        process_log(log_dir, &spec.id)?,
    )
}

fn spawn_command(
    arguments: &[String],
    cwd: Option<&Path>,
    environment: &HashMap<String, String>,
    stdout: File,
) -> io::Result<Child> {
    let stderr = stdout.try_clone()?;
    let mut command = Command::new(&arguments[0]);
    command
        .args(&arguments[1..])
        .env_clear()
        // Services opt into an EOF watchdog. Keeping this pipe owned by Child
        // makes an abrupt locald exit observable without inspecting or killing
        // unrelated system processes on the next launch.
        .stdin(Stdio::piped())
        .stdout(Stdio::from(stdout))
        .stderr(Stdio::from(stderr));
    for key in INHERITED_ENVIRONMENT {
        if let Some(value) = std::env::var_os(key) {
            command.env(key, value);
        }
    }
    command.envs(environment);
    if let Some(cwd) = cwd {
        command.current_dir(cwd);
    }
    #[cfg(unix)]
    {
        use std::os::unix::process::CommandExt;
        command.process_group(0);
    }
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        // CREATE_NEW_PROCESS_GROUP so the group can be signalled as a unit --
        // the Windows provider upgrades this to a Job Object before host packs
        // become the default -- plus CREATE_NO_WINDOW, because these are
        // console programs (python.exe, node.exe) started by a GUI app with no
        // console and each would otherwise be given a conhost window.
        //
        // Spelled out together because creation_flags replaces the flag set.
        command.creation_flags(crate::CREATE_NO_WINDOW | crate::CREATE_NEW_PROCESS_GROUP);
    }
    command.spawn()
}

#[cfg(windows)]
fn create_windows_job() -> io::Result<usize> {
    use windows_sys::Win32::System::JobObjects::{
        CreateJobObjectW, JobObjectExtendedLimitInformation, SetInformationJobObject,
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
    };

    let handle = unsafe { CreateJobObjectW(std::ptr::null(), std::ptr::null()) };
    if handle.is_null() {
        return Err(io::Error::last_os_error());
    }
    let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { std::mem::zeroed() };
    limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
    let configured = unsafe {
        SetInformationJobObject(
            handle,
            JobObjectExtendedLimitInformation,
            &limits as *const _ as *const _,
            std::mem::size_of_val(&limits) as u32,
        )
    };
    if configured == 0 {
        unsafe { windows_sys::Win32::Foundation::CloseHandle(handle) };
        return Err(io::Error::last_os_error());
    }
    Ok(handle as usize)
}

#[cfg(windows)]
fn assign_child_to_windows_job(job: usize, child: &mut Child) -> io::Result<()> {
    use std::os::windows::io::AsRawHandle;
    use windows_sys::Win32::System::JobObjects::AssignProcessToJobObject;
    let assigned = unsafe { AssignProcessToJobObject(job as _, child.as_raw_handle() as _) };
    if assigned == 0 {
        let error = io::Error::last_os_error();
        let _ = child.kill();
        let _ = child.wait();
        Err(error)
    } else {
        Ok(())
    }
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

/// Ceiling for one service's log before it is rotated, and therefore half the
/// most it can occupy: the rotated copy sits beside it at the same size.
const SERVICE_LOG_MAX_BYTES: u64 = 5 * 1024 * 1024;

/// How often a running service's log is measured. Long enough to be free, short
/// enough that a service logging hard cannot get far past the ceiling between
/// checks.
const SERVICE_LOG_ROTATE_INTERVAL: Duration = Duration::from_secs(30);

fn process_log(log_dir: &Path, id: &str) -> io::Result<File> {
    let path = log_dir.join(format!("{id}.log"));
    rotate_log(&path, SERVICE_LOG_MAX_BYTES)?;
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options.open(path)
}

fn rotate_log(path: &Path, max_bytes: u64) -> io::Result<()> {
    if path
        .metadata()
        .is_ok_and(|metadata| metadata.len() >= max_bytes)
    {
        // Copy aside and truncate in place, rather than rename and let a new
        // file appear. The writer is a running child holding this handle: after
        // a rename it keeps writing into the rotated file, so the live log
        // stops growing and the rotated one never stops. Removing the previous
        // file first also fails outright on Windows if anything still has it
        // open. Truncating the file the writer already holds moves it back to
        // zero without either problem.
        fs::copy(path, path.with_extension("previous.log"))?;
        OpenOptions::new().write(true).open(path)?.set_len(0)?;
    }
    Ok(())
}

#[cfg(unix)]
fn terminate_process_group(child: &mut Child) -> io::Result<()> {
    // Reap first. A child that has already exited still reports its old pid,
    // and on a busy machine that number gets recycled quickly -- so signalling
    // `-pid` here would reach whatever process group inherited it, which is at
    // best somebody else's processes and at worst our own unrelated services.
    // There is nothing to terminate in that case anyway.
    if child.try_wait()?.is_some() {
        return Ok(());
    }
    let process_group = -(child.id() as i32);
    // SAFETY: kill is called with a process group created for this exact child.
    let result = unsafe { libc::kill(process_group, libc::SIGTERM) };
    if result != 0 {
        let error = io::Error::last_os_error();
        // ESRCH means the group went away between the check above and the
        // signal. EPERM means the id now names processes that are not ours,
        // which says the same thing: ours are gone. Neither is a reason to
        // fail the stop -- doing so aborted `stop_all` partway through and
        // left the rest of the stack running.
        if !matches!(error.raw_os_error(), Some(libc::ESRCH) | Some(libc::EPERM)) {
            return Err(error);
        }
    }
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if child.try_wait()?.is_some() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    // SAFETY: same owned process group, now beyond graceful timeout.
    unsafe { libc::kill(process_group, libc::SIGKILL) };
    child.wait().map(|_| ())
}

#[cfg(windows)]
fn terminate_process_group(child: &mut Child) -> io::Result<()> {
    // This was `child.kill()`, which is TerminateProcess: no chance to flush,
    // no shutdown hook, and it does not touch descendants. The backend lost
    // in-flight writes and left Postgres with unclean disconnects on every
    // stop, and anything it or Next.js had spawned kept running -- and kept its
    // port -- until locald itself exited.
    //
    // Windows has no SIGTERM, and CREATE_NO_WINDOW gives each child its own
    // console, so GenerateConsoleCtrlEvent cannot reach them from here. What
    // does exist is the EOF watchdog services opt into: dropping our end of
    // their stdin closes the pipe, which is the same shutdown signal an abrupt
    // locald exit gives them.
    drop(child.stdin.take());
    let deadline = Instant::now() + Duration::from_secs(5);
    while Instant::now() < deadline {
        if child.try_wait()?.is_some() {
            return Ok(());
        }
        thread::sleep(Duration::from_millis(50));
    }
    // Beyond the graceful window. Take the tree, not just the child, so nothing
    // is left holding a port the next start needs.
    let felled = Command::new("taskkill")
        .no_console_window()
        .args(["/PID", &child.id().to_string(), "/T", "/F"])
        .status()
        .is_ok_and(|status| status.success());
    if !felled {
        child.kill()?;
    }
    child.wait().map(|_| ())
}

fn probe_http(spec: &HttpHealthSpec) -> io::Result<String> {
    let remainder = spec.url.strip_prefix("http://").ok_or_else(|| {
        io::Error::new(io::ErrorKind::InvalidInput, "host health URL must use http")
    })?;
    let (authority, path) = remainder.split_once('/').unwrap_or((remainder, ""));
    let (host, port) = authority.rsplit_once(':').ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "health URL must include a port",
        )
    })?;
    let port: u16 = port
        .parse()
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "invalid health port"))?;
    let addresses: Vec<SocketAddr> = (host, port).to_socket_addrs()?.collect();
    if addresses.is_empty() || addresses.iter().any(|address| !is_loopback(address.ip())) {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            "host health URL must resolve only to loopback",
        ));
    }
    let mut stream = TcpStream::connect_timeout(&addresses[0], Duration::from_secs(1))?;
    stream.set_read_timeout(Some(Duration::from_secs(2)))?;
    stream.set_write_timeout(Some(Duration::from_secs(2)))?;
    write!(
        stream,
        "GET /{} HTTP/1.1\r\nHost: {}\r\nConnection: close\r\n\r\n",
        path, authority
    )?;
    let mut response = Vec::new();
    let mut chunk = [0_u8; 4096];
    loop {
        match stream.read(&mut chunk) {
            Ok(0) => break,
            Ok(count) => {
                if response.len() + count > 64 * 1024 {
                    return Err(io::Error::new(
                        io::ErrorKind::InvalidData,
                        "health response exceeds 64 KiB",
                    ));
                }
                response.extend_from_slice(&chunk[..count]);
            }
            // Some small HTTP servers close with RST after sending the complete
            // response. Retain already-read bytes and validate the status/body
            // instead of discarding a legitimate bounded response.
            Err(error)
                if error.kind() == io::ErrorKind::ConnectionReset && !response.is_empty() =>
            {
                break;
            }
            Err(error) => return Err(error),
        }
    }
    let response_text = std::str::from_utf8(&response)
        .map_err(|_| io::Error::new(io::ErrorKind::InvalidData, "invalid health response"))?;
    let status_line = response_text.lines().next().unwrap_or_default();
    let status = status_line
        .split_whitespace()
        .nth(1)
        .and_then(|value| value.parse::<u16>().ok())
        .ok_or_else(|| io::Error::new(io::ErrorKind::InvalidData, "invalid health response"))?;
    if !(200..300).contains(&status) {
        return Err(io::Error::other(format!("health returned {status}")));
    }
    if let Some(expected) = spec.expected_body.as_deref() {
        if !response_text.contains(expected) {
            return Err(io::Error::new(
                io::ErrorKind::InvalidData,
                "health response came from a different runtime instance",
            ));
        }
    }
    let body = response_text
        .split_once("\r\n\r\n")
        .map(|(_, body)| body)
        .unwrap_or_default();
    Ok(body.to_owned())
}

fn wait_for_setup_dependency(
    setup: &HostSetupSpec,
    environment: &HashMap<String, String>,
    setup_deadline: Instant,
) -> io::Result<()> {
    if setup.id != "migrations" {
        return Ok(());
    }
    let Some(address) = environment
        .get("DATABASE_URL")
        .and_then(|url| database_socket_address(url))
    else {
        return Ok(());
    };

    // The VM readiness check runs before host setup, but a newly established
    // macOS route can still flap during the few milliseconds before asyncpg
    // opens its first connection. Gate every Alembic attempt on the exact
    // database endpoint it will use. Keep this bounded so a broken route
    // produces an actionable error instead of an apparent startup hang.
    let deadline = std::cmp::min(setup_deadline, Instant::now() + Duration::from_secs(30));
    loop {
        let error = match TcpStream::connect_timeout(&address, Duration::from_millis(750)) {
            Ok(_) => return Ok(()),
            Err(error) => error,
        };
        if Instant::now() >= deadline {
            return Err(io::Error::new(
                io::ErrorKind::TimedOut,
                format!(
                    "migrations could not reach PostgreSQL at {address} before setup; last error: {}",
                    error
                ),
            ));
        }
        thread::sleep(Duration::from_millis(250));
    }
}

fn database_socket_address(url: &str) -> Option<SocketAddr> {
    let (_, remainder) = url.split_once("://")?;
    let authority = remainder.split(['/', '?', '#']).next().unwrap_or_default();
    let host_and_port = authority.rsplit('@').next().unwrap_or(authority);
    let (host, port) = if let Some(bracketed) = host_and_port.strip_prefix('[') {
        let (host, suffix) = bracketed.split_once(']')?;
        let port = suffix.strip_prefix(':')?.parse::<u16>().ok()?;
        (host, port)
    } else if let Some((host, port)) = host_and_port.rsplit_once(':') {
        (host, port.parse::<u16>().ok()?)
    } else {
        (host_and_port, 5432)
    };
    let ip = host.parse::<IpAddr>().ok()?;
    Some(SocketAddr::new(ip, port))
}

fn tail_log(path: &Path, max_bytes: usize) -> Option<String> {
    let bytes = std::fs::read(path).ok()?;
    let start = bytes.len().saturating_sub(max_bytes);
    Some(String::from_utf8_lossy(&bytes[start..]).into_owned())
}

fn is_loopback(address: IpAddr) -> bool {
    address.is_loopback()
}

#[cfg(test)]
mod tests {
    use super::*;
    // Only the unix tests spawn a real supervised process to bind a port.
    #[cfg(unix)]
    use crate::port_reservation::PortReservation;
    use std::net::{Ipv4Addr, TcpListener};
    use tempfile::{tempdir, TempDir};

    /// The log directory to hand a manager under test: a directory *inside*
    /// `root`, never `root` itself.
    ///
    /// A manager takes its installation state root — `installation.id` and the
    /// process ledger — from the log directory's *parent*, because in
    /// production it is handed `<state root>/logs`. Passing `root.path()` here
    /// therefore made the parent the system temporary directory, so every
    /// manager in every test, and in every test binary running at the same
    /// time, shared one `$TMPDIR/processes.json` under one installation id.
    ///
    /// That is a live weapon: constructing a manager runs
    /// `reclaim_verified_processes`, which SIGTERMs any pid in the ledger that
    /// still matches its recorded executable and start time. With the ledger
    /// shared, one test's `HostProcessManager::new` killed the `/bin/sleep`
    /// services another test had spawned seconds earlier — which is exactly
    /// how `opens_restart_circuit_after_crash_budget_is_exhausted` came to
    /// fail on a loaded runner with its frontend dead 50ms after it started,
    /// and why it never failed on an idle machine, where the tests do not
    /// overlap. One directory deeper gives every manager its own installation,
    /// which is what the reclaim was written to assume.
    fn log_dir_in(root: &TempDir) -> PathBuf {
        root.path().join("logs")
    }

    /// A manager whose installation state stays inside `root`.
    fn manager_in(root: &TempDir, value: HostPackManifest) -> Arc<HostProcessManager> {
        // No supervisor thread. These tests step the state machine themselves,
        // and a second driver of it once a second is what made the restart
        // circuit tests fail on CI and never here.
        HostProcessManager::without_supervisor(value, log_dir_in(root)).unwrap()
    }

    /// A running service's log is truncated under the writer that holds it.
    ///
    /// This is the property the whole rotation scheme depends on and the reason
    /// it copies aside instead of renaming: the child owns this descriptor for
    /// its entire life, so a rename would leave it writing into the rotated
    /// file and the live one frozen at zero forever. Asserted with a handle
    /// still open, because that is the only state that ever actually occurs.
    #[test]
    fn a_live_service_log_is_rotated_under_the_process_writing_it() {
        use std::io::Write;

        let root = tempdir().unwrap();
        let path = root.path().join("backend.log");
        let mut writer = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&path)
            .unwrap();
        writer.write_all(&vec![b'x'; 1024]).unwrap();

        // Under the ceiling: left exactly alone.
        rotate_log(&path, 4096).unwrap();
        assert_eq!(path.metadata().unwrap().len(), 1024);
        assert!(!path.with_extension("previous.log").exists());

        // Over it: copied aside and truncated back to zero.
        writer.write_all(&vec![b'x'; 4096]).unwrap();
        rotate_log(&path, 4096).unwrap();
        assert_eq!(path.metadata().unwrap().len(), 0);
        assert_eq!(
            path.with_extension("previous.log")
                .metadata()
                .unwrap()
                .len(),
            5120
        );

        // And the writer that never let go keeps appending to the same file,
        // which is now counting up from zero rather than from 5 KiB.
        writer.write_all(b"after").unwrap();
        writer.flush().unwrap();
        assert_eq!(path.metadata().unwrap().len(), 5);
    }

    fn manifest(services: Vec<HostProcessSpec>) -> HostPackManifest {
        HostPackManifest {
            schema_version: 1,
            release: "test".into(),
            managed_runtime: None,
            setup: vec![setup("migrations")],
            services,
        }
    }

    fn setup(id: &str) -> HostSetupSpec {
        HostSetupSpec {
            id: id.into(),
            command: vec!["test-program".into()],
            cwd: None,
            env: HashMap::new(),
            timeout_seconds: 10,
            max_attempts: 3,
            retry_backoff_seconds: 0,
            optional: false,
            stamp: None,
        }
    }

    fn service(id: &str, dependencies: &[&str]) -> HostProcessSpec {
        HostProcessSpec {
            id: id.into(),
            command: vec!["test-program".into()],
            cwd: None,
            env: HashMap::new(),
            dependencies: dependencies.iter().map(|value| (*value).into()).collect(),
            health: None,
            restart: RestartSpec::default(),
        }
    }

    /// A service process that stays up until it is asked to stop.
    ///
    /// Spawned by absolute path and not through a shell, because the manager
    /// identifies a process by the executable `ps` reports for it. `sh -c
    /// "sleep 30"` replaces the shell with `sleep`, whose `argv[0]` is the bare
    /// word the shell resolved on `PATH` — and a bare word is not a path that
    /// canonicalizes, so ownership could only be recorded in the window before
    /// the child exec'd. That is a race against the child, and a loaded machine
    /// loses it.
    #[cfg(unix)]
    fn long_running_command() -> Vec<String> {
        vec!["/bin/sleep".into(), "30".into()]
    }

    /// A health endpoint that answers only after `delay_ms` of being asked, and
    /// reports when it first said yes.
    ///
    /// The delay runs from the first probe rather than from construction, so it
    /// models a service that takes a moment to come up rather than a deadline
    /// the test itself has to beat.
    ///
    /// The body is shared because the manager mints the runtime generation and
    /// rewrites every health spec's expected body to it, so what counts as
    /// healthy is not known until the generation exists.
    #[cfg(unix)]
    fn slow_response(
        delay_ms: u64,
        body: Arc<Mutex<String>>,
    ) -> (
        HttpHealthSpec,
        Arc<Mutex<Option<Instant>>>,
        thread::JoinHandle<()>,
    ) {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let served = Arc::clone(&body);
        let healthy_at = Arc::new(Mutex::new(None));
        let observed = Arc::clone(&healthy_at);
        let server = thread::spawn(move || {
            let mut ready_at = None;
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { break };
                let ready =
                    *ready_at.get_or_insert(Instant::now() + Duration::from_millis(delay_ms));
                if Instant::now() < ready {
                    // Refuse rather than answer: the prober retries, which is
                    // what a service that has not finished booting looks like.
                    drop(stream);
                    continue;
                }
                observed.lock().unwrap().get_or_insert_with(Instant::now);
                let payload = served.lock().unwrap().clone();
                let _ = stream.write_all(
                    format!(
                        "HTTP/1.1 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{payload}",
                        payload.len()
                    )
                    .as_bytes(),
                );
                let _ = stream.flush();
            }
        });
        (
            HttpHealthSpec {
                url: format!("http://{address}/health"),
                timeout_seconds: 30,
                expected_body: Some("placeholder".into()),
                stabilization_seconds: 0,
            },
            healthy_at,
            server,
        )
    }

    fn one_response(status: u16, body: &str) -> (HttpHealthSpec, thread::JoinHandle<()>) {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let body = body.to_owned();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            // Consume the whole request head before answering. A single read can
            // return before the client has finished writing, and a discarded read
            // error hides that entirely. Responding and then dropping the socket
            // while request bytes are still unread makes the kernel close with RST
            // instead of FIN, so the client's in-flight write fails with EPIPE
            // rather than reading the healthy response.
            stream
                .set_read_timeout(Some(Duration::from_secs(10)))
                .unwrap();
            let mut request = Vec::new();
            let mut byte = [0_u8; 1];
            while !request.ends_with(b"\r\n\r\n") {
                match stream.read(&mut byte) {
                    Ok(0) => break,
                    Ok(_) => request.extend_from_slice(&byte),
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                    Err(_) => break,
                }
            }
            write!(
                stream,
                "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
            stream.flush().unwrap();
            stream.shutdown(std::net::Shutdown::Write).unwrap();
            // Hold the socket open until the client has read the response and
            // closed its end, so the drop below is a graceful FIN rather than an
            // RST that could discard buffered response bytes mid-read.
            let mut drained = Vec::new();
            let _ = stream.read_to_end(&mut drained);
        });
        (
            HttpHealthSpec {
                url: format!("http://{address}/health"),
                timeout_seconds: 1,
                expected_body: Some("runtime-123".into()),
                stabilization_seconds: 0,
            },
            server,
        )
    }

    #[test]
    fn health_requires_two_xx_and_the_expected_runtime_identity() {
        for status in [401, 404, 503] {
            let (unhealthy, server) = one_response(status, "runtime-123");
            assert!(probe_http(&unhealthy).is_err());
            crate::join_within(server, "the health endpoint");
        }

        let (stale, stale_server) = one_response(200, "runtime-old");
        let error = probe_http(&stale).unwrap_err();
        assert!(
            error.to_string().contains("different runtime instance"),
            "{error}"
        );
        crate::join_within(stale_server, "the stale health endpoint");

        let (healthy, healthy_server) = one_response(200, "runtime-123");
        probe_http(&healthy).unwrap();
        crate::join_within(healthy_server, "the healthy endpoint");
    }

    #[test]
    fn validates_exact_two_process_contract_and_dependency_order() {
        let manifest = manifest(vec![
            service("frontend", &["backend"]),
            service("backend", &[]),
        ]);
        let order = validate_and_order(&manifest).unwrap();
        assert_eq!(order, vec!["backend", "frontend"]);
    }

    #[test]
    fn compatibility_host_packs_expose_app_ports_for_the_sharing_gateway() {
        let mut frontend = service("frontend", &["backend"]);
        frontend.health = Some(HttpHealthSpec {
            url: "http://127.0.0.1:3711/runtime-config.js".into(),
            timeout_seconds: 1,
            expected_body: None,
            stabilization_seconds: 0,
        });
        let mut backend = service("backend", &[]);
        backend.health = Some(HttpHealthSpec {
            url: "http://localhost:8711/health/ready".into(),
            timeout_seconds: 1,
            expected_body: None,
            stabilization_seconds: 0,
        });
        let root = tempdir().unwrap();
        let manager = manager_in(&root, manifest(vec![frontend, backend]));

        assert_eq!(manager.application_ports(), Some((3711, 8711)));
        assert_eq!(loopback_http_port("https://127.0.0.1:3711/"), None);
        assert_eq!(loopback_http_port("http://0.0.0.0:3711/"), None);
    }

    #[test]
    fn rejects_missing_processes_and_cycles() {
        let missing = manifest(vec![service("backend", &[])]);
        assert!(validate_and_order(&missing)
            .unwrap_err()
            .to_string()
            .contains("frontend"));

        let cycle = manifest(vec![
            service("backend", &["frontend"]),
            service("frontend", &["backend"]),
        ]);
        assert!(validate_and_order(&cycle)
            .unwrap_err()
            .to_string()
            .contains("cycle"));
    }

    #[test]
    fn rejects_missing_migration_setup() {
        let mut value = manifest(vec![
            service("backend", &[]),
            service("frontend", &["backend"]),
        ]);
        value.setup.clear();

        assert!(validate_and_order(&value)
            .unwrap_err()
            .to_string()
            .contains("migrations setup"));
    }

    #[test]
    fn operator_secrets_are_ephemeral_and_backend_scoped() {
        let root = tempdir().unwrap();
        let manager = manager_in(
            &root,
            manifest(vec![
                service("frontend", &["backend"]),
                service("backend", &[]),
            ]),
        );
        manager.set_backend_environment(HashMap::from([(
            "LEMMA_OPENAI_API_KEY".into(),
            "vault-secret".into(),
        )]));

        assert_eq!(
            manager.process_spec_for_spawn("backend").unwrap().env["LEMMA_OPENAI_API_KEY"],
            "vault-secret"
        );
        assert!(!manager
            .process_spec_for_spawn("frontend")
            .unwrap()
            .env
            .contains_key("LEMMA_OPENAI_API_KEY"));
        assert!(!manager.by_id["backend"]
            .env
            .contains_key("LEMMA_OPENAI_API_KEY"));
    }

    #[test]
    fn dependency_failure_clears_readiness_and_surfaces_the_cause() {
        let root = tempdir().unwrap();
        let manager = manager_in(
            &root,
            manifest(vec![
                service("frontend", &["backend"]),
                service("backend", &[]),
            ]),
        );
        manager.desired_running.store(true, Ordering::Release);
        manager.health_ready.store(true, Ordering::Release);

        manager.mark_dependency_unavailable("private VM exited".into());
        let failed = manager.status_event(None);
        assert_eq!(failed["ready"], false);
        assert_eq!(failed["status"], "error");
        assert_eq!(failed["dependency_error"], "private VM exited");

        manager.mark_dependency_recovering();
        let recovering = manager.status_event(None);
        assert_eq!(recovering["status"], "starting");
        assert!(recovering["dependency_error"].is_null());

        manager.mark_dependency_ready();
        let recovered = manager.status_event(None);
        assert_eq!(recovered["dependency_ready"], true);
        assert!(recovered["dependency_error"].is_null());
    }

    #[cfg(unix)]
    #[test]
    fn migration_setup_receives_the_same_dynamic_backend_environment() {
        let root = tempdir().unwrap();
        let mut value = manifest(vec![
            service("frontend", &["backend"]),
            service("backend", &[]),
        ]);
        value.setup[0].command = vec!["/usr/bin/env".into()];
        let manager = manager_in(&root, value);
        manager.set_backend_environment(HashMap::from([(
            "DATABASE_URL".into(),
            "postgresql://private-guest/lemma".into(),
        )]));

        manager.run_setups().unwrap();

        let log = std::fs::read_to_string(log_dir_in(&root).join("migrations.log")).unwrap();
        assert!(log.contains("DATABASE_URL=postgresql://private-guest/lemma"));
    }

    #[cfg(unix)]
    #[test]
    fn an_optional_setup_that_fails_does_not_stop_the_stack() {
        // Seeding the connector catalog reaches the network whenever a Composio
        // key is set. A workspace that will not start because a third-party
        // catalog was unreachable would be a bad trade for a feature this
        // session may not even use, so the failure is logged and the start
        // continues. Migrations stay required: a backend running against a
        // schema it does not expect is worse than one that refuses to start.
        let root = tempdir().unwrap();
        let mut value = manifest(vec![
            service("frontend", &["backend"]),
            service("backend", &[]),
        ]);
        value.setup[0].command = vec!["/bin/sh".into(), "-c".into(), "exit 9".into()];
        value.setup[0].max_attempts = 1;
        value.setup[0].optional = true;
        let manager = manager_in(&root, value);

        manager
            .run_setups()
            .expect("an optional setup must not fail the start");
    }

    #[cfg(unix)]
    #[test]
    fn a_required_setup_that_fails_still_stops_the_stack() {
        let root = tempdir().unwrap();
        let mut value = manifest(vec![
            service("frontend", &["backend"]),
            service("backend", &[]),
        ]);
        value.setup[0].command = vec!["/bin/sh".into(), "-c".into(), "exit 9".into()];
        value.setup[0].max_attempts = 1;
        value.setup[0].optional = false;
        let manager = manager_in(&root, value);

        assert!(manager.run_setups().is_err());
    }

    #[cfg(unix)]
    #[test]
    fn migration_setup_retries_a_transient_cold_guest_failure() {
        let root = tempdir().unwrap();
        let marker = root.path().join("route-ready");
        let mut value = manifest(vec![
            service("frontend", &["backend"]),
            service("backend", &[]),
        ]);
        value.setup[0].command = vec![
            "/bin/sh".into(),
            "-c".into(),
            "if [ -f \"$1\" ]; then exit 0; fi; touch \"$1\"; exit 65".into(),
            "lemma-migration-retry".into(),
            marker.to_string_lossy().into_owned(),
        ];
        value.setup[0].max_attempts = 2;
        let manager = manager_in(&root, value);

        manager.run_setups().unwrap();

        let log = std::fs::read_to_string(log_dir_in(&root).join("migrations.log")).unwrap();
        assert!(log.contains("setup attempt 1 exited"));
        assert!(marker.is_file());
    }

    #[test]
    fn parses_only_literal_database_endpoints_for_the_private_route_gate() {
        assert_eq!(
            database_socket_address(
                "postgresql+asyncpg://postgres:secret@192.168.64.10:5432/lemma"
            ),
            Some("192.168.64.10:5432".parse().unwrap())
        );
        assert_eq!(
            database_socket_address("postgresql://postgres:secret@127.0.0.1/lemma"),
            Some("127.0.0.1:5432".parse().unwrap())
        );
        assert_eq!(
            database_socket_address("postgresql://private-guest/lemma"),
            None
        );
    }

    #[cfg(unix)]
    #[test]
    fn migration_setup_waits_for_its_exact_database_route() {
        let root = tempdir().unwrap();
        let reservation = PortReservation::ephemeral().unwrap();
        let address = reservation.address();
        let mut value = manifest(vec![
            service("frontend", &["backend"]),
            service("backend", &[]),
        ]);
        value.setup[0].command = vec!["/usr/bin/true".into()];
        value.setup[0].timeout_seconds = 5;
        let manager = manager_in(&root, value);
        manager.set_backend_environment(HashMap::from([(
            "DATABASE_URL".into(),
            format!("postgresql://postgres:secret@{address}/lemma"),
        )]));

        // The route opens only after the setup has started waiting, which is the
        // behaviour under test. Everything here is bounded: a blocking `accept()`
        // joined unconditionally hangs the whole test binary forever whenever the
        // probe does not connect exactly once, which is how this burned 44
        // minutes of a CI runner before it was cancelled rather than failing.
        let route = thread::spawn(move || {
            thread::sleep(Duration::from_millis(150));
            // The port stays reserved for the whole wait, so no other test in
            // this binary can be handed it. Until this line the reservation is
            // bound but not listening, so `run_setups` is refused exactly as an
            // absent route would refuse it; here the same socket starts
            // listening, so the route opens without the port ever being free.
            let listener = reservation.listen().unwrap();
            listener.set_nonblocking(true).unwrap();
            let deadline = Instant::now() + Duration::from_secs(10);
            while Instant::now() < deadline {
                match listener.accept() {
                    Ok(_) => return true,
                    Err(error) if error.kind() == io::ErrorKind::WouldBlock => {
                        thread::sleep(Duration::from_millis(10));
                    }
                    Err(_) => return false,
                }
            }
            false
        });
        manager.run_setups().unwrap();
        assert!(
            route.join().unwrap(),
            "run_setups should have connected to the database route it was told to wait for"
        );
    }

    #[cfg(unix)]
    #[test]
    fn starts_and_stops_backend_and_frontend_process_groups() {
        let command = vec![
            "/bin/sh".into(),
            "-c".into(),
            "trap 'exit 0' TERM; while :; do sleep 1; done".into(),
        ];
        let mut backend = service("backend", &[]);
        backend.command = command.clone();
        let mut frontend = service("frontend", &["backend"]);
        frontend.command = command;
        let root = tempdir().unwrap();
        let mut value = manifest(vec![frontend, backend]);
        value.setup[0].command = vec!["/usr/bin/true".into()];
        let manager = manager_in(&root, value);

        manager.start_all().unwrap();
        assert!(manager.status().iter().all(|process| process.running));
        assert_eq!(manager.status_event(None)["ready"], true);
        manager.stop_all().unwrap();
        assert!(manager.status().iter().all(|process| !process.running));
        assert_eq!(manager.status_event(None)["ready"], false);
    }

    /// `/proc/<pid>/stat` field 22, past a command name that fights back.
    ///
    /// This is the parse the Linux ownership ledger depends on, and it has one
    /// trap: field 2 is the command name in parentheses and a process may name
    /// itself anything at all, spaces and parentheses included. Splitting the
    /// whole line on whitespace mis-numbers every field after it -- which would
    /// mean recording a nonsense start identity, which would mean an ownership
    /// record that never matches and a child this daemon can never reclaim.
    ///
    /// Run on every platform on purpose: the failure it guards is a string
    /// parse, and the machine that most needs it is the one that cannot run
    /// the code around it.
    #[test]
    fn a_process_start_time_survives_a_command_name_full_of_parentheses() {
        let stat = |comm: &str| {
            let mut fields = vec!["4242".to_string(), format!("({comm})")];
            // Fields 3..21, then starttime at 22.
            fields.push("S".into());
            fields.extend((4..=21).map(|field| field.to_string()));
            fields.push("987654".into());
            // And the tail the kernel keeps writing after it.
            fields.extend((23..=30).map(|field| field.to_string()));
            fields.join(" ") + "\n"
        };

        assert_eq!(
            process_start_time(&stat("sleep")).as_deref(),
            Some("987654")
        );
        assert_eq!(
            process_start_time(&stat("my (weird) name")).as_deref(),
            Some("987654"),
            "the split has to happen after the LAST close paren"
        );
        assert_eq!(
            process_start_time(&stat("node --run start")).as_deref(),
            Some("987654"),
            "a name with spaces must not shift the field numbering"
        );
        // A truncated read is not a start identity, and must not be treated as
        // one -- an empty identity matches nothing and would be recorded as if
        // it did.
        assert_eq!(process_start_time("4242 (sleep) S 4 5"), None);
        assert_eq!(process_start_time("nonsense with no paren"), None);
        assert_eq!(process_start_time(""), None);
    }

    /// A manager that goes out of scope takes its services with it.
    ///
    /// `stop_all()` on the success path used to be the only thing that stopped
    /// them, so a test that panicked before reaching it -- and these tests
    /// assert on live process state, which is exactly what flakes under load --
    /// left two `sh` loops running forever, each forking a `sleep` every
    /// second. `spawn_command` sets `process_group(0)`, so closing the terminal
    /// sends them nothing, and the ownership ledger that could reclaim them is
    /// in a `TempDir` that is already gone. This is how a laptop ends up warm
    /// for a week.
    ///
    /// Two things had to change for this to be assertable at all: the `Drop`
    /// existed only on Windows, and the monitor thread held an `Arc` of the
    /// manager, so the refcount never reached zero and no `Drop` could fire.
    #[cfg(unix)]
    #[test]
    fn dropping_a_manager_stops_the_services_it_started() {
        let root = tempdir().unwrap();
        let mut backend = service("backend", &[]);
        backend.command = long_running_command();
        let mut frontend = service("frontend", &["backend"]);
        frontend.command = long_running_command();
        let mut value = manifest(vec![backend, frontend]);
        value.setup[0].command = vec!["/usr/bin/true".into()];

        let pids: Vec<u32> = {
            let manager = manager_in(&root, value);
            manager.start_all().unwrap();
            let pids = manager
                .status()
                .iter()
                .filter_map(|process| process.pid)
                .collect();
            // No `stop_all`. This is the unwind path, written as a scope.
            pids
        };

        assert_eq!(pids.len(), 2, "both services should have been running");
        // Asserted on the process *group*, which is what `spawn_command`
        // creates and what would still hold the `sleep` children.
        for pid in pids {
            let group = i32::try_from(pid).expect("a pid fits in i32");
            let deadline = Instant::now() + Duration::from_secs(5);
            while unsafe { libc::kill(-group, 0) } == 0 && Instant::now() < deadline {
                thread::sleep(Duration::from_millis(50));
            }
            assert_ne!(
                unsafe { libc::kill(-group, 0) },
                0,
                "process group {group} outlived the manager that started it",
            );
        }
    }

    /// The stamp decision itself, on every platform.
    ///
    /// The four tests that prove this end to end spawn `/bin/sh`, so they are
    /// `#[cfg(unix)]` -- three failed on Windows for exactly that reason, and
    /// the fourth passed there without proving anything. This is the half that
    /// needs no process, and it is the half that decides whether a migration
    /// runs.
    #[test]
    fn a_setup_reruns_unless_its_exact_stamp_was_recorded() {
        let recorded = |value: &str| Some(value.to_owned());

        // No stamp is how a setup opts out of this entirely.
        assert!(!setup_is_already_done(None, None));
        assert!(!setup_is_already_done(
            None,
            recorded("release-0.7.0").as_ref()
        ));

        // Never run before.
        assert!(!setup_is_already_done(Some("release-0.7.0"), None));

        // Run before, same work.
        assert!(setup_is_already_done(
            Some("release-0.7.0"),
            recorded("release-0.7.0").as_ref()
        ));

        // Run before, different work: a new pack, or migrations that changed
        // inside one. Skipping here is a backend starting against tables that
        // were never created.
        assert!(!setup_is_already_done(
            Some("release-0.8.0"),
            recorded("release-0.7.0").as_ref()
        ));
        // And no accidental prefix or case matching.
        assert!(!setup_is_already_done(
            Some("release-0.7.0"),
            recorded("release-0.7.0-rc1").as_ref()
        ));
        assert!(!setup_is_already_done(
            Some("release-0.7.0"),
            recorded("RELEASE-0.7.0").as_ref()
        ));
    }

    /// Every test that drives a real process is gated to the platforms that
    /// can drive one.
    ///
    /// The helpers below -- `long_running_command`, `crash`, `wait_for_running`,
    /// `wait_for_recorded_exit` -- are `#[cfg(unix)]`, because they signal
    /// process groups and shell out to `sh`. A test that uses one without the
    /// same gate does not fail on Windows, it fails to *compile*, and the only
    /// place that shows up is the Windows CI job -- which is not in the desktop
    /// filter, so the feedback arrives a push or two later. It has now cost
    /// three round trips.
    ///
    /// A source lint rather than a convention, in the shape `lib.rs` already
    /// uses for the console-window rule.
    #[test]
    fn a_test_that_drives_a_real_process_is_gated_to_unix() {
        const HELPERS: [&str; 4] = [
            "long_running_command(",
            "crash(&",
            "wait_for_running(&",
            "wait_for_recorded_exit(&",
        ];
        // A POSIX binary is the other way a test needs a unix host, and it is
        // the one that cost the third round: four stamp tests ran `/bin/sh` and
        // `/usr/bin/false`. Three failed on Windows with "The system cannot
        // find the path specified"; the fourth *passed*, because it asserts the
        // setup fails and a missing binary fails too -- proving nothing, in
        // green.
        const POSIX_BINARIES: [&str; 3] = ["\"/bin/", "\"/usr/bin/", "\"/sbin/"];
        // Every source file in this crate, not just this one. Both rounds of
        // Windows failures were in here, but the next one need not be -- and a
        // lint that only reads its own file is a lint that moves the problem.
        let sources: [(&str, String); 6] = [
            (
                "host_process.rs",
                include_str!("host_process.rs").replace("\r\n", "\n"),
            ),
            (
                "agent_host.rs",
                include_str!("agent_host.rs").replace("\r\n", "\n"),
            ),
            ("daemon.rs", include_str!("daemon.rs").replace("\r\n", "\n")),
            (
                "sharing.rs",
                include_str!("sharing.rs").replace("\r\n", "\n"),
            ),
            (
                "network.rs",
                include_str!("network.rs").replace("\r\n", "\n"),
            ),
            (
                "managed_runtime.rs",
                include_str!("managed_runtime.rs").replace("\r\n", "\n"),
            ),
        ];

        let mut ungated = Vec::new();
        for (file, source) in &sources {
            let lines: Vec<&str> = source.lines().collect();
            for (index, line) in lines.iter().enumerate() {
                let Some(name) = line.trim().strip_prefix("fn ") else {
                    continue;
                };
                // A test function: `#[test]` on one of the few lines above it.
                let preamble = &lines[index.saturating_sub(4)..index];
                if !preamble.iter().any(|line| line.trim() == "#[test]") {
                    continue;
                }
                let gated = preamble.iter().any(|line| line.trim() == "#[cfg(unix)]");
                if gated {
                    continue;
                }
                // The body, to its closing brace at the same indentation.
                let body: String = lines[index..]
                    .iter()
                    .take_while(|line| !line.starts_with("    }"))
                    .copied()
                    .collect::<Vec<_>>()
                    .join("\n");
                let name = name.split('(').next().unwrap_or(name);
                // This test names the helpers in order to look for them.
                if name == "a_test_that_drives_a_real_process_is_gated_to_unix" {
                    continue;
                }
                let needs_unix = HELPERS.iter().any(|helper| body.contains(helper))
                    || POSIX_BINARIES.iter().any(|path| body.contains(path));
                if needs_unix {
                    ungated.push(format!("{file}::{name}"));
                }
            }
        }

        assert!(
            ungated.is_empty(),
            "these tests need a unix host -- a helper that signals a process \
             group, or a POSIX binary to run -- and are not #[cfg(unix)]. On \
             Windows they either fail to compile or fail to find the binary: \
             {ungated:?}",
        );
    }

    #[cfg(unix)]
    #[test]
    fn process_ledger_reclaims_only_an_exact_owned_process() {
        use std::os::unix::process::ExitStatusExt;

        let root = tempdir().unwrap();
        let ledger_path = root.path().join("processes.json");
        let installation_id = "0123456789abcdef0123456789abcdef";
        let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
        let identity = process_identity(child.id()).unwrap();
        let mut backend = service("backend", &[]);
        backend.command = vec!["/bin/sleep".into(), "30".into()];
        let value = manifest(vec![backend, service("frontend", &["backend"])]);
        write_process_ledger(
            &ledger_path,
            &ProcessLedger {
                schema_version: PROCESS_LEDGER_SCHEMA_VERSION,
                installation_id: installation_id.into(),
                entries: vec![ProcessLedgerEntry {
                    service_id: "backend".into(),
                    pid: child.id(),
                    executable: identity.executable,
                    start_identity: identity.start_identity,
                    installation_id: installation_id.into(),
                    runtime_generation: "0123456789abcdef0123456789abcdef".into(),
                }],
            },
        )
        .unwrap();

        // Reaped in parallel, which is the whole trick.
        //
        // `terminate_verified_process` signals, then waits for the PID to stop
        // existing before escalating. A dead child nobody has reaped is a
        // zombie, and a zombie still answers `kill(pid, 0)` -- so this test's
        // process could never be observed to exit, the wait ran its full five
        // seconds every time, and the assertion that followed was left racing
        // whatever the runner did next. Production never has this problem: a
        // reclaimed process belonged to a previous locald and is reaped by
        // init, so its PID really does go away.
        //
        // Reaping here restores that, and the exit status is then an exact
        // answer rather than a deadline: signalled means reclaimed, and a
        // process that was missed runs out its own 30 seconds and fails
        // saying so.
        let reaper = thread::spawn(move || child.wait().unwrap());
        reclaim_verified_processes(&ledger_path, installation_id, &value).unwrap();
        let status = reaper.join().unwrap();
        assert!(
            status.signal().is_some(),
            "the reclaimed process exited on its own rather than being killed: \
             {status:?}",
        );
        assert!(read_process_ledger(&ledger_path)
            .unwrap()
            .entries
            .is_empty());
    }

    #[cfg(unix)]
    #[test]
    fn process_ledger_never_kills_a_pid_with_the_wrong_start_identity() {
        let root = tempdir().unwrap();
        let ledger_path = root.path().join("processes.json");
        let installation_id = "0123456789abcdef0123456789abcdef";
        let mut child = Command::new("/bin/sleep").arg("30").spawn().unwrap();
        let identity = process_identity(child.id()).unwrap();
        let mut backend = service("backend", &[]);
        backend.command = vec!["/bin/sleep".into(), "30".into()];
        let value = manifest(vec![backend, service("frontend", &["backend"])]);
        write_process_ledger(
            &ledger_path,
            &ProcessLedger {
                schema_version: PROCESS_LEDGER_SCHEMA_VERSION,
                installation_id: installation_id.into(),
                entries: vec![ProcessLedgerEntry {
                    service_id: "backend".into(),
                    pid: child.id(),
                    executable: identity.executable,
                    start_identity: "different-process-start".into(),
                    installation_id: installation_id.into(),
                    runtime_generation: "0123456789abcdef0123456789abcdef".into(),
                }],
            },
        )
        .unwrap();

        reclaim_verified_processes(&ledger_path, installation_id, &value).unwrap();

        assert!(child.try_wait().unwrap().is_none());
        child.kill().unwrap();
        child.wait().unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn startup_reports_child_exit_and_recent_log_without_waiting_for_health_timeout() {
        let mut backend = service("backend", &[]);
        backend.command = vec![
            "/bin/sh".into(),
            "-c".into(),
            "echo exact-backend-failure; exit 17".into(),
        ];
        backend.health = Some(HttpHealthSpec {
            url: "http://127.0.0.1:9/health".into(),
            timeout_seconds: 30,
            expected_body: Some("runtime-123".into()),
            stabilization_seconds: 0,
        });
        let frontend = service("frontend", &["backend"]);
        let root = tempdir().unwrap();
        let mut value = manifest(vec![frontend, backend]);
        value.setup[0].command = vec!["/usr/bin/true".into()];
        let manager = manager_in(&root, value);

        let started = Instant::now();
        let error = manager.start_all().unwrap_err().to_string();

        assert!(started.elapsed() < Duration::from_secs(5));
        assert!(error.contains("process exited"));
        assert!(error.contains("exact-backend-failure"));
    }

    #[cfg(unix)]
    #[test]
    fn services_boot_alongside_each_other_rather_than_one_after_another() {
        // The frontend used to be spawned only after the backend passed its
        // full health gate, which put its entire boot on the critical path for
        // no reason: `next start` serves a prebuilt app and does not wait on
        // the backend. What that cost is not a number of milliseconds but an
        // ordering — the second service could not begin until the first had
        // finished — so that is what this asserts. A total-elapsed budget would
        // instead be a claim about the machine, and this suite runs its tests
        // against each other.
        let body = Arc::new(Mutex::new(String::new()));
        let (backend_health, backend_healthy_at, backend_server) =
            slow_response(700, Arc::clone(&body));
        let (frontend_health, frontend_healthy_at, frontend_server) =
            slow_response(700, Arc::clone(&body));
        let mut backend = service("backend", &[]);
        backend.command = long_running_command();
        backend.health = Some(backend_health);
        let mut frontend = service("frontend", &["backend"]);
        frontend.command = long_running_command();
        frontend.health = Some(frontend_health);

        let root = tempdir().unwrap();
        let mut value = manifest(vec![frontend, backend]);
        value.setup[0].command = vec!["/usr/bin/true".into()];
        let manager = manager_in(&root, value);
        // The production path mints the generation before starting, and every
        // health spec is rewritten to expect it.
        *body.lock().unwrap() = manager.prepare_runtime_generation().unwrap();

        // The manager announces each service as it reaches it, which is the
        // only account of when a boot began that does not depend on guessing.
        let mut boot_started = HashMap::new();
        manager
            .start_all_with_progress(|stage| {
                boot_started.insert(stage.to_owned(), Instant::now());
            })
            .unwrap();
        manager.stop_all().unwrap();
        drop(backend_server);
        drop(frontend_server);

        // The first health gate to pass is the earliest moment any service can
        // be said to have finished booting. Both had to be under way by then.
        let first_healthy = [&backend_healthy_at, &frontend_healthy_at]
            .into_iter()
            .filter_map(|healthy_at| *healthy_at.lock().unwrap())
            .min()
            .expect("a service answered its health gate");
        for id in REQUIRED_SERVICES {
            let started = boot_started[id];
            assert!(
                started < first_healthy,
                "{id} only began booting {:?} after another service was already healthy",
                started.saturating_duration_since(first_healthy),
            );
        }
    }

    /// A service that came up early must not restart the dwell clock late.
    ///
    /// Each gate requires `stabilization_seconds` of *continuously observed*
    /// health, measured from the first probe that gate itself saw succeed. Run
    /// one after another, that charges the dwell once per service: the frontend
    /// is healthy within a fraction of a second and then waits, idle and
    /// unwatched, for the backend's gate to finish -- and only then does its own
    /// gate start counting, spending the full window again on a service that
    /// had been healthy the whole time.
    ///
    /// Asserted on the *gap* between the two services' first probes, not on how
    /// long startup took. An absolute bound is a claim about the machine: this
    /// began life as "under 1800ms", passed locally at 1.28s, and failed a CI
    /// runner at 4.31s while the gates were concurrent exactly as intended. The
    /// gap is the thing the defect is actually about, and a slow machine slows
    /// both services together, so it stays true wherever it runs.
    #[cfg(unix)]
    #[test]
    fn one_slow_service_does_not_make_every_other_service_wait_its_dwell_again() {
        let body = Arc::new(Mutex::new(String::new()));
        let (mut backend_health, backend_probed, backend_server) =
            slow_response(0, Arc::clone(&body));
        let (mut frontend_health, frontend_probed, frontend_server) =
            slow_response(0, Arc::clone(&body));
        backend_health.stabilization_seconds = 1;
        frontend_health.stabilization_seconds = 1;

        let mut backend = service("backend", &[]);
        backend.command = long_running_command();
        backend.health = Some(backend_health);
        let mut frontend = service("frontend", &["backend"]);
        frontend.command = long_running_command();
        frontend.health = Some(frontend_health);

        let root = tempdir().unwrap();
        let mut value = manifest(vec![frontend, backend]);
        value.setup[0].command = vec!["/usr/bin/true".into()];
        let manager = manager_in(&root, value);
        *body.lock().unwrap() = manager.prepare_runtime_generation().unwrap();

        manager.start_all().unwrap();
        manager.stop_all().unwrap();
        drop(backend_server);
        drop(frontend_server);

        let backend_probed = backend_probed.lock().unwrap().expect("backend was probed");
        let frontend_probed = frontend_probed
            .lock()
            .unwrap()
            .expect("frontend was probed");

        // Both gates start watching together, so both services see their first
        // probe at about the same moment. Serially, the second is not probed
        // until the first has finished its whole dwell -- a second later, by
        // construction, whatever the machine.
        let gap = if backend_probed > frontend_probed {
            backend_probed - frontend_probed
        } else {
            frontend_probed - backend_probed
        };
        assert!(
            gap < Duration::from_millis(500),
            "the two services were first probed {gap:?} apart, which is about \
             the 1s dwell -- the gates are being charged one after another \
             rather than watched together",
        );
    }

    #[cfg(unix)]
    #[test]
    fn backend_config_restart_keeps_the_frontend_process_running() {
        let mut backend = service("backend", &[]);
        backend.command = long_running_command();
        let mut frontend = service("frontend", &["backend"]);
        frontend.command = long_running_command();
        let root = tempdir().unwrap();
        let mut value = manifest(vec![frontend, backend]);
        value.setup[0].command = vec!["/usr/bin/true".into()];
        let manager = manager_in(&root, value);
        manager.start_all().unwrap();
        assert!(manager.backend_restart_available());
        manager.mark_dependency_unavailable("private runtime is cold".into());
        assert!(!manager.backend_restart_available());
        manager.mark_dependency_ready();
        assert!(manager.backend_restart_available());
        // Named rather than unwrapped: when this fails on a loaded machine the
        // useful question is *which* process lost its pid, and a bare unwrap
        // answers neither that nor what the rest of the stack was doing.
        fn pids(manager: &HostProcessManager, when: &str) -> HashMap<String, u32> {
            let status = manager.status();
            status
                .iter()
                .map(|process| {
                    let pid = process.pid.unwrap_or_else(|| {
                        panic!("{} has no pid {when}; full status: {status:#?}", process.id)
                    });
                    (process.id.clone(), pid)
                })
                .collect()
        }

        let before = pids(&manager, "before the restart");

        manager.restart_backend().unwrap();

        let after = pids(&manager, "after the restart");
        assert_ne!(before["backend"], after["backend"]);
        assert_eq!(before["frontend"], after["frontend"]);
        manager.stop_all().unwrap();
    }

    #[cfg(unix)]
    fn process_status(manager: &HostProcessManager, id: &str) -> HostProcessStatus {
        manager
            .status()
            .into_iter()
            .find(|process| process.id == id)
            .unwrap_or_else(|| panic!("{id} is not a managed service"))
    }

    /// Blocks until the supervisor reports `id` running, and answers with its
    /// pid.
    ///
    /// The mirror of `wait_for_recorded_exit`, and needed for the same reason.
    /// "Started" is not a moment the caller of `start_all` or `reconcile_crashes`
    /// observes — a spawn is recorded when the supervisor gets to it — so a test
    /// that reads the pid straight afterwards is racing it. That read is not
    /// even the assertion in most cases; it is the setup for one, so on a loaded
    /// runner these failed as `backend is not running` from inside a helper,
    /// several lines away from anything the test was actually about.
    #[cfg(unix)]
    fn wait_for_running(manager: &HostProcessManager, id: &str) -> u32 {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            if let Some(pid) = process_status(manager, id).pid {
                return pid;
            }
            assert!(
                Instant::now() < deadline,
                "{id} never started; the supervisor still reports no process for it"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    /// Kills a running service the way a crash takes one: no notice, no chance
    /// to shut down. The whole process group goes, so nothing of it survives to
    /// be reported as still running.
    ///
    /// Waits for the service to be up first, so "crash it" means what it says
    /// whether or not the supervisor has caught up with a start or a restart.
    #[cfg(unix)]
    fn crash(manager: &HostProcessManager, id: &str) {
        let pid = wait_for_running(manager, id);
        let group = -i32::try_from(pid).expect("process id fits a signed integer");
        // SAFETY: the pid names a child this manager spawned into its own
        // process group and has not yet reaped, so the group is still ours.
        assert_eq!(
            unsafe { libc::kill(group, libc::SIGKILL) },
            0,
            "could not crash {id}: {}",
            io::Error::last_os_error()
        );
    }

    /// Blocks until the supervisor has recorded that `id` exited.
    ///
    /// Reporting status is what reaps an exited child, so this is the same
    /// observation the supervision loop makes — waited for, rather than assumed
    /// to have happened by the end of a sleep that a loaded machine outruns.
    #[cfg(unix)]
    fn wait_for_recorded_exit(manager: &HostProcessManager, id: &str) {
        let deadline = Instant::now() + Duration::from_secs(10);
        while process_status(manager, id).running {
            assert!(
                Instant::now() < deadline,
                "{id} never reported the exit it was killed for"
            );
            thread::sleep(Duration::from_millis(10));
        }
    }

    /// Reconcile until the supervisor reaches `what`, or fail saying what it
    /// actually reached.
    ///
    /// Drive to the state, do not count the steps. Two calls happen to be
    /// what a restart costs today -- one to spend the budget, one to act on it
    /// -- but that is an implementation detail of `reconcile_crashes`, and a
    /// test that hard-codes it asserts against whichever state the count lands
    /// on rather than the one it means. Waiting for the state also fails
    /// legibly: it says what was actually reached instead of `assertion failed:
    /// !backend.running`, which is all CI got.
    ///
    /// `manager_in` builds these without a supervisor (see
    /// `without_supervisor`), so nothing else is stepping the machine while
    /// this runs.
    ///
    /// Unix-gated because `process_status` is: it reaps, which needs waitpid.
    #[cfg(unix)]
    fn reconcile_until(
        manager: &HostProcessManager,
        id: &str,
        what: &str,
        reached: impl Fn(&HostProcessStatus) -> bool,
    ) -> HostProcessStatus {
        let deadline = Instant::now() + Duration::from_secs(10);
        loop {
            manager.reconcile_crashes();
            let status = process_status(manager, id);
            if reached(&status) {
                return status;
            }
            assert!(
                Instant::now() < deadline,
                "{id} never {what}. running={} circuit_open={} restart_count={} \
                 last_exit={:?}",
                status.running,
                status.circuit_open,
                status.restart_count,
                status.last_exit,
            );
            thread::sleep(Duration::from_millis(20));
        }
    }

    /// The supervisor restarts a crashed service on its own.
    ///
    /// The one thing about it that production depends on, and until this test
    /// nothing asserted it: every other test drives `reconcile_crashes` by
    /// hand, so the thread that calls it once a second could have failed to
    /// spawn at all and the suite would have stayed green.
    ///
    /// Waits for the restart instead of timing it. That direction is safe --
    /// a slow machine only makes the wait longer, never wrong -- which is the
    /// distinction that made the circuit tests flaky: they counted the
    /// supervisor's steps, and a loaded runner gave it more of them.
    #[cfg(unix)]
    #[test]
    fn the_supervisor_restarts_a_crashed_service_with_nobody_driving_it() {
        let mut backend = service("backend", &[]);
        backend.command = long_running_command();
        backend.restart = RestartSpec {
            max_restarts: 5,
            window_seconds: 60,
            backoff_seconds: 0,
        };
        // A frontend too: the manifest is required to have one.
        let mut frontend = service("frontend", &["backend"]);
        frontend.command = long_running_command();
        let root = tempdir().unwrap();
        let mut value = manifest(vec![frontend, backend]);
        value.setup[0].command = vec!["/usr/bin/true".into()];
        // Supervised, unlike `manager_in` -- the thread is the subject here.
        let manager = HostProcessManager::new(value, log_dir_in(&root)).unwrap();

        manager.start_all().unwrap();
        let first = wait_for_running(&manager, "backend");

        crash(&manager, "backend");

        // Nothing is called on `manager` from here: if it comes back, the
        // supervisor is what brought it back. It ticks once a second, so this
        // deadline is ~15 ticks of slack.
        let deadline = Instant::now() + Duration::from_secs(15);
        loop {
            let status = process_status(&manager, "backend");
            if status.running && status.pid != Some(first) {
                break;
            }
            assert!(
                Instant::now() < deadline,
                "supervisor never restarted the crashed backend \
                 (running={} pid={:?} was={:?} restart_count={})",
                status.running,
                status.pid,
                Some(first),
                status.restart_count,
            );
            thread::sleep(Duration::from_millis(50));
        }
    }

    #[cfg(unix)]
    #[test]
    fn opens_restart_circuit_after_crash_budget_is_exhausted() {
        let mut backend = service("backend", &[]);
        backend.command = long_running_command();
        backend.restart = RestartSpec {
            max_restarts: 1,
            window_seconds: 60,
            backoff_seconds: 0,
        };
        let mut frontend = service("frontend", &["backend"]);
        frontend.command = long_running_command();
        let root = tempdir().unwrap();
        let mut value = manifest(vec![frontend, backend]);
        value.setup[0].command = vec!["/usr/bin/true".into()];
        let manager = manager_in(&root, value);

        manager.start_all().unwrap();
        // Both, before anything is crashed. The frontend depends on the backend
        // and is asserted at the end to have survived the backend's circuit
        // opening — an assertion that reads as a real failure when in fact the
        // frontend had never finished starting.
        wait_for_running(&manager, "backend");
        wait_for_running(&manager, "frontend");

        // One restart is budgeted, so it takes two crashes to exhaust it. Each
        // crash is followed by the supervisor actually observing the exit,
        // because reconciling one it has not seen yet is a no-op, and a run
        // that does that silently ends up asserting against a supervisor still
        // a step behind.
        crash(&manager, "backend");
        wait_for_recorded_exit(&manager, "backend");
        reconcile_until(&manager, "backend", "used its one budgeted restart", |s| {
            s.running
        });

        crash(&manager, "backend");
        wait_for_recorded_exit(&manager, "backend");
        let backend = reconcile_until(
            &manager,
            "backend",
            "opened its circuit with the budget exhausted",
            |s| s.circuit_open,
        );
        assert!(
            !backend.running,
            "an open circuit must not leave it running"
        );
        assert_eq!(backend.restart_count, 1);
        assert!(backend.last_exit.is_some());
        assert!(process_status(&manager, "frontend").running);
        manager.stop_all().unwrap();
    }

    /// A tripped circuit reopens once the crash window has gone quiet.
    ///
    /// It never used to. `reconcile_crashes` tested `circuit_open` before it
    /// pruned `restart_history`, so the history was frozen the moment the
    /// circuit tripped and no amount of elapsed time could clear it. A single
    /// transient burst — a laptop waking before the VM's port forwarders are
    /// back — condemned the service until the user restarted the whole app,
    /// and nothing on screen said so.
    ///
    /// The window has to outlast the two crashes that exhaust the budget --
    /// otherwise the first restart ages out before the second crash and the
    /// budget silently resets, which is a green test proving nothing. Four
    /// seconds is comfortably longer than a spawn plus two observed exits, and
    /// short enough that waiting it out does not dominate the suite.
    // Unix-only for the same reason as its sibling above: `crash` signals a
    // process group and `long_running_command` is a shell one-liner.
    #[cfg(unix)]
    #[test]
    fn a_tripped_restart_circuit_reopens_after_a_quiet_window() {
        let mut backend = service("backend", &[]);
        backend.command = long_running_command();
        backend.restart = RestartSpec {
            max_restarts: 1,
            window_seconds: 4,
            backoff_seconds: 0,
        };
        let mut frontend = service("frontend", &[]);
        frontend.command = long_running_command();
        let root = tempdir().unwrap();
        let mut value = manifest(vec![frontend, backend]);
        value.setup[0].command = vec!["/usr/bin/true".into()];
        let manager = manager_in(&root, value);

        manager.start_all().unwrap();
        wait_for_running(&manager, "backend");

        crash(&manager, "backend");
        wait_for_recorded_exit(&manager, "backend");
        reconcile_until(&manager, "backend", "used its one budgeted restart", |s| {
            s.running
        });

        crash(&manager, "backend");
        wait_for_recorded_exit(&manager, "backend");
        reconcile_until(
            &manager,
            "backend",
            "opened its circuit with the budget exhausted",
            |s| s.circuit_open,
        );

        // Nothing deliberate happens here — only the window elapsing.
        thread::sleep(Duration::from_millis(4500));
        reconcile_until(&manager, "backend", "reopened its circuit", |s| {
            !s.circuit_open
        });

        let backend = process_status(&manager, "backend");
        assert!(
            !backend.circuit_open,
            "a quiet window reopens the circuit without an app restart"
        );
        // ...but the trip is remembered. This is the durable half: without it,
        // a service that trips once per window would report healthy in every
        // gap and oscillate the splash between "error" and "starting".
        //
        // Deliberately not asserted through `status_event` here. Once the
        // service actually comes back, `ready` wins over `failed` and the
        // status is legitimately "running" -- so an assertion on the status
        // string would be racing the very recovery this test is proving works.
        // The trip count is what the UI keys on while a component is still
        // down, and it is what this pins.
        assert_eq!(backend.circuit_trips, 1);

        manager.stop_all().unwrap();
    }

    /// A remembered trip keeps a component reading as failed after its circuit
    /// closes.
    ///
    /// Asserted on the predicate rather than through a live manager: the state
    /// that matters -- circuit closed again, trip remembered, service still
    /// down -- exists for a fraction of a second in a real supervisor, and a
    /// test that raced it would be a flake pretending to be coverage.
    #[test]
    fn a_component_that_has_tripped_still_reads_as_failed_after_the_circuit_closes() {
        let component = |circuit_open: bool, circuit_trips: u32| HostProcessStatus {
            id: "backend".into(),
            running: false,
            pid: None,
            circuit_open,
            circuit_trips,
            restart_count: 0,
            last_exit: None,
        };

        assert!(
            !HostProcessManager::components_report_failure(&[component(false, 0)]),
            "a component that has never tripped is not a failure"
        );
        assert!(
            HostProcessManager::components_report_failure(&[component(true, 1)]),
            "an open circuit is a failure"
        );
        assert!(
            HostProcessManager::components_report_failure(&[component(false, 1)]),
            "a closed circuit with a remembered trip is still a failure, or a \
             service that flaps once a window would read healthy in every gap"
        );
    }

    /// The generation a start reports is the generation it runs.
    ///
    /// `prepare_runtime_generation` and `start_all_inner` used to disagree on a
    /// partially-running stack -- one saw "some children" and kept the old
    /// value, the other saw "not all children" and minted a new one. Every
    /// phase, state and ready event then carried the old generation while the
    /// services ran the new one.
    ///
    /// That value decides, on the next launch, whether the workspace recorded
    /// last time is still the one serving. A mixture made that question
    /// answerable by processes from two different runs.
    #[cfg(unix)]
    #[test]
    fn a_partially_running_stack_reports_the_generation_it_actually_runs() {
        let mut backend = service("backend", &[]);
        backend.command = long_running_command();
        let mut frontend = service("frontend", &[]);
        frontend.command = long_running_command();
        let root = tempdir().unwrap();
        let mut value = manifest(vec![frontend, backend]);
        value.setup[0].command = vec!["/usr/bin/true".into()];
        let manager = manager_in(&root, value);

        manager.start_all().unwrap();
        wait_for_running(&manager, "backend");
        wait_for_running(&manager, "frontend");

        // Kill one service, leaving the stack partially up -- the state a user
        // is in when they press Start after a crash loop.
        crash(&manager, "frontend");
        wait_for_recorded_exit(&manager, "frontend");

        let announced = manager.prepare_runtime_generation().unwrap();
        manager.start_all().unwrap();
        wait_for_running(&manager, "backend");
        wait_for_running(&manager, "frontend");

        let running = manager.status_event(None)["runtime_generation"]
            .as_str()
            .unwrap()
            .to_owned();
        assert_eq!(
            announced, running,
            "the generation announced to the app must be the one the services were given"
        );
        manager.stop_all().unwrap();
    }

    /// A stamped setup runs once and is skipped while its stamp holds.
    ///
    /// Both setups ran on every start. The cost is not the SQL -- alembic's
    /// no-op is one `SELECT` -- it is `env.py` importing the whole ORM graph
    /// before it can decide there is nothing to do, on every launch.
    #[cfg(unix)]
    #[test]
    fn a_stamped_setup_is_not_repeated_while_its_stamp_holds() {
        let root = tempdir().unwrap();
        let marker = root.path().join("ran");
        let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
        value.setup[0].command = vec![
            "/bin/sh".into(),
            "-c".into(),
            format!("echo x >> {}", marker.display()),
        ];
        value.setup[0].stamp = Some("release-0.7.0".into());
        let manager = manager_in(&root, value);

        manager.run_setups().unwrap();
        manager.run_setups().unwrap();
        manager.run_setups().unwrap();

        let runs = std::fs::read_to_string(&marker).unwrap().lines().count();
        assert_eq!(runs, 1, "a stamped setup runs once, not once per start");
    }

    /// A changed stamp runs it again; so does an unstamped setup.
    #[cfg(unix)]
    #[test]
    fn a_changed_stamp_runs_the_setup_again() {
        let root = tempdir().unwrap();
        let marker = root.path().join("ran");
        let command = vec![
            "/bin/sh".into(),
            "-c".into(),
            format!("echo x >> {}", marker.display()),
        ];

        let mut first = manifest(vec![service("backend", &[]), service("frontend", &[])]);
        first.setup[0].command = command.clone();
        first.setup[0].stamp = Some("release-0.7.0".into());
        manager_in(&root, first).run_setups().unwrap();

        // A new release: the migrations it ships are not the ones already run.
        let mut second = manifest(vec![service("backend", &[]), service("frontend", &[])]);
        second.setup[0].command = command.clone();
        second.setup[0].stamp = Some("release-0.8.0".into());
        manager_in(&root, second).run_setups().unwrap();

        // No stamp at all behaves exactly as before stamps existed.
        let mut third = manifest(vec![service("backend", &[]), service("frontend", &[])]);
        third.setup[0].command = command;
        third.setup[0].stamp = None;
        manager_in(&root, third).run_setups().unwrap();

        assert_eq!(std::fs::read_to_string(&marker).unwrap().lines().count(), 3);
    }

    /// A failed setup is never stamped.
    ///
    /// Stamping anything but success would let a half-finished migration be
    /// skipped on the next start, which is strictly worse than running it
    /// again.
    #[cfg(unix)]
    #[test]
    fn a_failing_setup_is_not_stamped_as_done() {
        let root = tempdir().unwrap();
        let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
        value.setup[0].command = vec!["/usr/bin/false".into()];
        value.setup[0].stamp = Some("release-0.7.0".into());
        value.setup[0].max_attempts = 1;
        let manager = manager_in(&root, value);

        assert!(manager.run_setups().is_err());
        assert!(
            manager.recorded_setup_stamps().is_empty(),
            "a setup that failed must run again next time"
        );
    }

    /// A data reset makes every setup run again.
    ///
    /// The database the migrations stamp describes is gone, but a Tier 1 reset
    /// leaves the locald root standing -- so without forgetting the stamps the
    /// next start would skip migrations against an empty schema and the backend
    /// would come up against tables that were never created.
    #[cfg(unix)]
    #[test]
    fn forgetting_stamps_makes_a_reset_installation_migrate_again() {
        let root = tempdir().unwrap();
        let marker = root.path().join("ran");
        let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
        value.setup[0].command = vec![
            "/bin/sh".into(),
            "-c".into(),
            format!("echo x >> {}", marker.display()),
        ];
        value.setup[0].stamp = Some("release-0.7.0".into());
        let manager = manager_in(&root, value);

        manager.run_setups().unwrap();
        manager.forget_setup_stamps().unwrap();
        manager.run_setups().unwrap();

        assert_eq!(std::fs::read_to_string(&marker).unwrap().lines().count(), 2);
        // Clearing twice is what a retried reset does; it must not fail.
        manager.forget_setup_stamps().unwrap();
    }

    /// An optional setup that *hangs* is tolerated, exactly like one that fails.
    ///
    /// `optional` was honoured at only one of the three places a setup can
    /// fail. A non-zero exit was swallowed; running out of time was not — so
    /// `connector-catalog`, which is declared optional with a 600-second budget
    /// precisely so an unreachable third-party catalog cannot stop a workspace,
    /// took the whole stack down whenever the network blackholed instead of
    /// refusing.
    #[cfg(unix)]
    #[test]
    fn an_optional_setup_that_hangs_does_not_stop_the_stack() {
        let root = tempdir().unwrap();
        let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
        value.setup[0].command = vec!["/usr/bin/true".into()];
        let mut catalog = setup("connector-catalog");
        catalog.command = long_running_command();
        catalog.optional = true;
        catalog.timeout_seconds = 1;
        catalog.max_attempts = 1;
        value.setup.push(catalog);
        let manager = manager_in(&root, value);

        manager
            .run_setups()
            .expect("an optional setup that never finishes must not fail the start");
    }

    /// The same hang, not marked optional, still stops the start.
    ///
    /// Without this the test above would pass just as well against a
    /// `run_setups` that had stopped enforcing timeouts at all.
    #[cfg(unix)]
    #[test]
    fn a_required_setup_that_hangs_still_stops_the_stack() {
        let root = tempdir().unwrap();
        let mut value = manifest(vec![service("backend", &[]), service("frontend", &[])]);
        value.setup[0].command = long_running_command();
        value.setup[0].optional = false;
        value.setup[0].timeout_seconds = 1;
        value.setup[0].max_attempts = 1;
        let manager = manager_in(&root, value);

        let error = manager.run_setups().unwrap_err();
        assert_eq!(error.kind(), io::ErrorKind::TimedOut);
        assert!(error.to_string().contains("migrations"), "{error}");
    }
}
