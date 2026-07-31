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

struct ProcessIdentity {
    executable: String,
    start_identity: String,
}

#[derive(Default)]
struct ProcessState {
    children: HashMap<String, ManagedChild>,
    restart_history: HashMap<String, VecDeque<Instant>>,
    restart_not_before: HashMap<String, Instant>,
    circuit_open: HashSet<String>,
    last_exit: HashMap<String, String>,
}

#[derive(Clone, Debug, Serialize)]
pub struct HostProcessStatus {
    pub id: String,
    pub running: bool,
    pub pid: Option<u32>,
    pub circuit_open: bool,
    pub restart_count: usize,
    pub last_exit: Option<String>,
}

pub struct HostProcessManager {
    manifest: HostPackManifest,
    ordered_ids: Vec<String>,
    by_id: HashMap<String, HostProcessSpec>,
    state: Mutex<ProcessState>,
    backend_environment: Mutex<HashMap<String, String>>,
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
        let monitor = Arc::clone(&manager);
        thread::spawn(move || loop {
            thread::sleep(Duration::from_secs(1));
            monitor.reconcile_crashes();
        });
        Ok(manager)
    }

    pub fn release(&self) -> &str {
        &self.manifest.release
    }

    pub fn managed_runtime(&self) -> Option<&ManagedRuntimeSpec> {
        self.manifest.managed_runtime.as_ref()
    }

    pub fn desired_running(&self) -> bool {
        self.desired_running.load(Ordering::Acquire)
    }

    pub fn set_backend_environment(&self, environment: HashMap<String, String>) {
        *self
            .backend_environment
            .lock()
            .expect("backend environment lock poisoned") = environment;
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
        let has_children = !self
            .state
            .lock()
            .expect("host process lock poisoned")
            .children
            .is_empty();
        let mut generation = self
            .runtime_generation
            .lock()
            .expect("runtime generation lock poisoned");
        if !has_children {
            *generation = random_generation()?;
            self.generation_prepared.store(true, Ordering::Release);
        }
        Ok(generation.clone())
    }

    fn start_all_inner(&self, progress: &mut dyn FnMut(&str)) -> io::Result<()> {
        self.inspect_exits();
        if self
            .state
            .lock()
            .expect("host process lock poisoned")
            .children
            .len()
            == self.ordered_ids.len()
        {
            self.desired_running.store(true, Ordering::Release);
            for id in &self.ordered_ids {
                if let Some(health) = self.health_spec(id) {
                    self.wait_process_health(id, &health).map_err(|error| {
                        io::Error::other(format!("{id} failed health gate: {error}"))
                    })?;
                }
            }
            self.verify_all_health_now()?;
            self.health_ready.store(true, Ordering::Release);
            return Ok(());
        }
        self.health_ready.store(false, Ordering::Release);
        self.desired_running.store(false, Ordering::Release);
        if !self.generation_prepared.swap(false, Ordering::AcqRel) {
            *self
                .runtime_generation
                .lock()
                .expect("runtime generation lock poisoned") = random_generation()?;
        }
        {
            let mut state = self.state.lock().expect("host process lock poisoned");
            state.circuit_open.clear();
            state.restart_history.clear();
            state.restart_not_before.clear();
        }

        progress("migrations");
        self.run_setups()?;
        self.desired_running.store(true, Ordering::Release);
        for id in &self.ordered_ids {
            progress(id);
            self.release_idle_port_for(id);
            if let Err(error) = self.spawn_if_missing(id) {
                let _ = self.stop_all();
                return Err(error);
            }
            if let Some(health) = self.health_spec(id) {
                if let Err(error) = self.wait_process_health(id, &health) {
                    let _ = self.stop_all();
                    return Err(io::Error::other(format!(
                        "{id} failed health gate: {error}"
                    )));
                }
            }
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

    fn run_setups(&self) -> io::Result<()> {
        'setups: for setup in &self.manifest.setup {
            let mut environment = setup.env.clone();
            environment.extend(
                self.backend_environment
                    .lock()
                    .expect("backend environment lock poisoned")
                    .clone(),
            );
            let deadline = Instant::now() + Duration::from_secs(setup.timeout_seconds);
            for attempt in 1..=setup.max_attempts {
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
                            continue 'setups;
                        }
                        if attempt == setup.max_attempts {
                            return Err(io::Error::other(format!(
                                "{} setup exited with {status} after {attempt} attempts; see {}",
                                setup.id,
                                self.log_dir.join(format!("{}.log", setup.id)).display()
                            )));
                        }
                        let backoff = Duration::from_secs(setup.retry_backoff_seconds);
                        if Instant::now() + backoff >= deadline {
                            return Err(io::Error::other(format!(
                                "{} setup exited with {status}; see {}",
                                setup.id,
                                self.log_dir.join(format!("{}.log", setup.id)).display()
                            )));
                        }
                        writeln!(
                            process_log(&self.log_dir, &setup.id)?,
                            "lemma-locald: setup attempt {attempt} exited with {status}; retrying"
                        )?;
                        thread::sleep(backoff);
                        break;
                    }
                    if Instant::now() >= deadline {
                        let _ = terminate_process_group(&mut child);
                        return Err(io::Error::new(
                            io::ErrorKind::TimedOut,
                            format!(
                                "{} setup exceeded {} seconds; see {}",
                                setup.id,
                                setup.timeout_seconds,
                                self.log_dir.join(format!("{}.log", setup.id)).display()
                            ),
                        ));
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
        let failed = components.iter().any(|component| component.circuit_open)
            || (desired && dependency_error.is_some());
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
        #[allow(unused_mut)]
        let mut child = spawn_process(&spec, &self.log_dir)?;
        #[cfg(windows)]
        assign_child_to_windows_job(self.windows_job, &mut child)?;
        if let Err(error) = self.record_child(id, &child) {
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
                if state.children.contains_key(id)
                    || state.circuit_open.contains(id)
                    || spec
                        .dependencies
                        .iter()
                        .any(|dependency| !state.children.contains_key(dependency))
                {
                    false
                } else {
                    let now = Instant::now();
                    if let Some(deadline) = state.restart_not_before.get(id) {
                        *deadline <= now
                    } else {
                        let history = state.restart_history.entry(id.clone()).or_default();
                        let window = Duration::from_secs(spec.restart.window_seconds);
                        while history
                            .front()
                            .is_some_and(|started| now.duration_since(*started) > window)
                        {
                            history.pop_front();
                        }
                        if history.len() >= spec.restart.max_restarts {
                            state.circuit_open.insert(id.clone());
                            false
                        } else {
                            history.push_back(now);
                            state.restart_not_before.insert(
                                id.clone(),
                                now + Duration::from_secs(spec.restart.backoff_seconds),
                            );
                            false
                        }
                    }
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

    fn record_child(&self, id: &str, child: &Child) -> io::Result<()> {
        let _guard = self
            .process_ledger_lock
            .lock()
            .expect("process ledger lock poisoned");
        let identity = process_identity(child.id())?;
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
    #[cfg(windows)]
    if destination.exists() {
        fs::remove_file(destination)?;
    }
    #[cfg(not(windows))]
    let _ = destination;
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

#[cfg(unix)]
fn process_identity(pid: u32) -> io::Result<ProcessIdentity> {
    let pid = pid.to_string();
    let executable = Command::new("/bin/ps")
        .args(["-p", &pid, "-o", "comm="])
        .output()?;
    let started = Command::new("/bin/ps")
        .args(["-p", &pid, "-o", "lstart="])
        .output()?;
    if !executable.status.success() || !started.status.success() {
        return Err(io::Error::new(io::ErrorKind::NotFound, "process not found"));
    }
    let executable = String::from_utf8(executable.stdout)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?;
    let executable = Path::new(executable.trim())
        .canonicalize()?
        .to_string_lossy()
        .into_owned();
    let start_identity = String::from_utf8(started.stdout)
        .map_err(|error| io::Error::new(io::ErrorKind::InvalidData, error))?
        .trim()
        .to_owned();
    if start_identity.is_empty() {
        return Err(io::Error::other("process start identity was empty"));
    }
    Ok(ProcessIdentity {
        executable,
        start_identity,
    })
}

#[cfg(unix)]
fn terminate_verified_process(pid: u32) -> io::Result<()> {
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
fn process_identity(pid: u32) -> io::Result<ProcessIdentity> {
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
fn terminate_verified_process(pid: u32) -> io::Result<()> {
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
        // CREATE_NEW_PROCESS_GROUP. The Windows provider upgrades this to a
        // Job Object before host packs become the default.
        command.creation_flags(0x0000_0200);
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

#[cfg(windows)]
impl Drop for HostProcessManager {
    fn drop(&mut self) {
        if self.windows_job != 0 {
            unsafe {
                windows_sys::Win32::Foundation::CloseHandle(self.windows_job as _);
            }
            self.windows_job = 0;
        }
    }
}

fn process_log(log_dir: &Path, id: &str) -> io::Result<File> {
    let path = log_dir.join(format!("{id}.log"));
    rotate_log(&path, 5 * 1024 * 1024)?;
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
        let previous = path.with_extension("previous.log");
        let _ = fs::remove_file(&previous);
        fs::rename(path, previous)?;
    }
    Ok(())
}

#[cfg(unix)]
fn terminate_process_group(child: &mut Child) -> io::Result<()> {
    let process_group = -(child.id() as i32);
    // SAFETY: kill is called with a process group created for this exact child.
    let result = unsafe { libc::kill(process_group, libc::SIGTERM) };
    if result != 0 {
        let error = io::Error::last_os_error();
        if error.raw_os_error() != Some(libc::ESRCH) {
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
    child.kill()?;
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
    use std::net::{Ipv4Addr, TcpListener};
    use tempfile::tempdir;

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

    fn one_response(status: u16, body: &str) -> (HttpHealthSpec, thread::JoinHandle<()>) {
        let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0)).unwrap();
        let address = listener.local_addr().unwrap();
        let body = body.to_owned();
        let server = thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0_u8; 1024];
            let _ = stream.read(&mut request);
            write!(
                stream,
                "HTTP/1.1 {status} Test\r\nContent-Length: {}\r\nConnection: close\r\n\r\n{body}",
                body.len()
            )
            .unwrap();
            stream.flush().unwrap();
            stream.shutdown(std::net::Shutdown::Write).unwrap();
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
            server.join().unwrap();
        }

        let (stale, stale_server) = one_response(200, "runtime-old");
        let error = probe_http(&stale).unwrap_err();
        assert!(
            error.to_string().contains("different runtime instance"),
            "{error}"
        );
        stale_server.join().unwrap();

        let (healthy, healthy_server) = one_response(200, "runtime-123");
        probe_http(&healthy).unwrap();
        healthy_server.join().unwrap();
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
        let manager = HostProcessManager::new(
            manifest(vec![
                service("frontend", &["backend"]),
                service("backend", &[]),
            ]),
            root.path().into(),
        )
        .unwrap();
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
        let manager = HostProcessManager::new(
            manifest(vec![
                service("frontend", &["backend"]),
                service("backend", &[]),
            ]),
            root.path().into(),
        )
        .unwrap();
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
        let manager = HostProcessManager::new(value, root.path().into()).unwrap();
        manager.set_backend_environment(HashMap::from([(
            "DATABASE_URL".into(),
            "postgresql://private-guest/lemma".into(),
        )]));

        manager.run_setups().unwrap();

        let log = std::fs::read_to_string(root.path().join("migrations.log")).unwrap();
        assert!(log.contains("DATABASE_URL=postgresql://private-guest/lemma"));
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
        let manager = HostProcessManager::new(value, root.path().into()).unwrap();

        manager.run_setups().unwrap();

        let log = std::fs::read_to_string(root.path().join("migrations.log")).unwrap();
        assert!(log.contains("setup attempt 1 exited"));
        assert!(marker.is_file());
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
        let manager = HostProcessManager::new(value, root.path().into()).unwrap();

        manager.start_all().unwrap();
        assert!(manager.status().iter().all(|process| process.running));
        assert_eq!(manager.status_event(None)["ready"], true);
        manager.stop_all().unwrap();
        assert!(manager.status().iter().all(|process| !process.running));
        assert_eq!(manager.status_event(None)["ready"], false);
    }

    #[cfg(unix)]
    #[test]
    fn process_ledger_reclaims_only_an_exact_owned_process() {
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

        reclaim_verified_processes(&ledger_path, installation_id, &value).unwrap();

        let deadline = Instant::now() + Duration::from_secs(2);
        while child.try_wait().unwrap().is_none() && Instant::now() < deadline {
            thread::sleep(Duration::from_millis(20));
        }
        assert!(child.try_wait().unwrap().is_some());
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
        let manager = HostProcessManager::new(value, root.path().into()).unwrap();

        let started = Instant::now();
        let error = manager.start_all().unwrap_err().to_string();

        assert!(started.elapsed() < Duration::from_secs(5));
        assert!(error.contains("process exited"));
        assert!(error.contains("exact-backend-failure"));
    }

    #[cfg(unix)]
    #[test]
    fn backend_config_restart_keeps_the_frontend_process_running() {
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
        let manager = HostProcessManager::new(value, root.path().into()).unwrap();
        manager.start_all().unwrap();
        let before: HashMap<_, _> = manager
            .status()
            .into_iter()
            .map(|process| (process.id, process.pid.unwrap()))
            .collect();

        manager.restart_backend().unwrap();

        let after: HashMap<_, _> = manager
            .status()
            .into_iter()
            .map(|process| (process.id, process.pid.unwrap()))
            .collect();
        assert_ne!(before["backend"], after["backend"]);
        assert_eq!(before["frontend"], after["frontend"]);
        manager.stop_all().unwrap();
    }

    #[cfg(unix)]
    #[test]
    fn opens_restart_circuit_after_crash_budget_is_exhausted() {
        let mut backend = service("backend", &[]);
        backend.command = vec!["/bin/sh".into(), "-c".into(), "exit 17".into()];
        backend.restart = RestartSpec {
            max_restarts: 1,
            window_seconds: 60,
            backoff_seconds: 0,
        };
        let mut frontend = service("frontend", &["backend"]);
        frontend.command = vec![
            "/bin/sh".into(),
            "-c".into(),
            "trap 'exit 0' TERM; while :; do sleep 1; done".into(),
        ];
        let root = tempdir().unwrap();
        let mut value = manifest(vec![frontend, backend]);
        value.setup[0].command = vec!["/usr/bin/true".into()];
        let manager = HostProcessManager::new(value, root.path().into()).unwrap();

        manager.start_all().unwrap();
        thread::sleep(Duration::from_millis(50));
        manager.reconcile_crashes();
        manager.reconcile_crashes();
        thread::sleep(Duration::from_millis(50));
        manager.reconcile_crashes();

        let backend = manager
            .status()
            .into_iter()
            .find(|process| process.id == "backend")
            .unwrap();
        assert!(!backend.running);
        assert!(backend.circuit_open);
        assert_eq!(backend.restart_count, 1);
        assert!(backend.last_exit.is_some());
        manager.stop_all().unwrap();
    }
}
