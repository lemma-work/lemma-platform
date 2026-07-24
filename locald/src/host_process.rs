use std::collections::{HashMap, HashSet, VecDeque};
use std::fs::{File, OpenOptions};
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
        let idle_port_reservations = reserve_managed_app_ports(&manifest)?;
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
        let result = self.start_all_inner();
        self.startup_in_progress.store(false, Ordering::Release);
        result
    }

    fn start_all_inner(&self) -> io::Result<()> {
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
                if let Some(health) = &self.by_id[id].health {
                    self.wait_process_health(id, health).map_err(|error| {
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
        {
            let mut state = self.state.lock().expect("host process lock poisoned");
            state.circuit_open.clear();
            state.restart_history.clear();
            state.restart_not_before.clear();
        }

        self.run_setups()?;
        self.desired_running.store(true, Ordering::Release);
        for id in &self.ordered_ids {
            self.release_idle_port_for(id);
            if let Err(error) = self.spawn_if_missing(id) {
                let _ = self.stop_all();
                return Err(error);
            }
            if let Some(health) = &self.by_id[id].health {
                if let Err(error) = self.wait_process_health(id, health) {
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
            if let Some(health) = &self.by_id[id].health {
                let mut health = health.clone();
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
        if let Some(health) = &self.by_id["backend"].health {
            if let Err(error) = self.wait_process_health("backend", health) {
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
            .by_id
            .get("backend")
            .and_then(|backend| backend.health.clone())
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
        let child = spawn_process(&spec, &self.log_dir)?;
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
        Ok(spec)
    }

    fn stop_process(&self, id: &str) -> io::Result<()> {
        let child = self
            .state
            .lock()
            .expect("host process lock poisoned")
            .children
            .remove(id);
        match child {
            Some(mut child) => terminate_process_group(&mut child.child),
            None => Ok(()),
        }
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
        if !reservations.contains_key(&runtime.ports.backend) {
            let listener = bind_idle_port(runtime.ports.backend)?;
            reservations.insert(runtime.ports.backend, listener);
        }
        if !reservations.contains_key(&runtime.ports.frontend) {
            let listener = bind_idle_port(runtime.ports.frontend)?;
            reservations.insert(runtime.ports.frontend, listener);
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
            state.last_exit.insert(id, exit);
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
                    if let Some(health) = &spec.health {
                        if let Err(error) = self.wait_process_health(id, health) {
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
}

fn sensitive_key(key: &str) -> bool {
    let key = key.to_ascii_lowercase();
    ["password", "secret", "token", "api_key", "apikey"]
        .iter()
        .any(|marker| key.contains(marker))
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

fn process_log(log_dir: &Path, id: &str) -> io::Result<File> {
    let mut options = OpenOptions::new();
    options.create(true).append(true);
    #[cfg(unix)]
    {
        use std::os::unix::fs::OpenOptionsExt;
        options.mode(0o600);
    }
    options.open(log_dir.join(format!("{id}.log")))
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
