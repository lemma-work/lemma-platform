//! What the shell is told about the stack.

use super::*;

/// How long a capabilities answer is reused for.
///
/// Long enough that a 1 Hz status loop stops being a 1 Hz HTTP client, short
/// enough that a capability coming up -- an AI provider configured, the
/// sandbox fabric becoming usable -- reaches a watching window promptly.
const CAPABILITIES_TTL: Duration = Duration::from_secs(10);

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

impl HostProcessManager {
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

    /// Every loopback port this stack is known to serve: the managed
    /// runtime's ports, and the port of each service's loopback health URL.
    ///
    /// The loopback relay refuses these, so a new service that declares a
    /// health check is refused without anybody remembering to add it.
    pub(crate) fn declared_loopback_ports(&self) -> Vec<u16> {
        let mut ports: Vec<u16> = self
            .by_id
            .values()
            .filter_map(|service| service.health.as_ref())
            .filter_map(|health| loopback_http_port(&health.url))
            .collect();
        if let Some(runtime) = self.manifest.managed_runtime.as_ref() {
            let runtime = &runtime.ports;
            ports.extend([
                runtime.backend,
                runtime.frontend,
                runtime.postgres,
                runtime.redis,
                runtime.supertokens,
            ]);
        }
        ports
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
        self.cached_capabilities().ok()
    }

    /// The capabilities answer, reused if it was fetched recently.
    ///
    /// The status monitor builds a status event every second and the event
    /// embeds this, so fetching it *is* an HTTP request: a fresh TCP
    /// connection with `Connection: close`, 86,400 times a day for as long as
    /// the app is open. What it returns is settings plus two capability
    /// probes, which do not change second to second.
    ///
    /// A failure is not cached. Whatever made the backend unreachable is the
    /// thing a watcher most wants to see change, so the next tick asks again.
    pub(crate) fn cached_capabilities(&self) -> io::Result<Value> {
        // Bound and dropped before the fetch below, which waits on a socket:
        // holding the cache lock across it would make every other status
        // reader wait for the backend too.
        let remembered = self
            .capabilities_cache
            .lock()
            .ok()
            .and_then(|cache| cache.clone())
            .filter(|(fetched_at, _)| fetched_at.elapsed() < CAPABILITIES_TTL)
            .map(|(_, value)| value);
        if let Some(value) = remembered {
            return Ok(value);
        }
        let fresh = self.capabilities_result()?;
        let mut cache = self
            .capabilities_cache
            .lock()
            .expect("capabilities cache lock poisoned");
        *cache = Some((Instant::now(), fresh.clone()));
        drop(cache);
        Ok(fresh)
    }

    pub(crate) fn capabilities_result(&self) -> io::Result<Value> {
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
}
