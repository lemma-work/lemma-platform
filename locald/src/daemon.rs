use std::collections::HashMap;
use std::env;
use std::io::{self, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;

use interprocess::local_socket::{prelude::*, ListenerOptions};
use serde_json::{json, Value};

use crate::host_process::HostProcessManager;
use crate::managed_runtime::{ManagedRuntimeBootstrap, ManagedRuntimeController};
use crate::native_host_pack;
use crate::operator_config::{ApplyOperatorConfig, OperatorConfigStore};
use crate::paths::LocalPaths;
use crate::protocol::{
    append_bounded_journal, authenticate, error_event, load_or_create_token, read_bounded_line,
};
use crate::state::StateSnapshot;
use crate::PROTOCOL_VERSION;

const DAEMON_VERSION: &str = env!("CARGO_PKG_VERSION");

struct SupervisorProcess {
    child: Child,
    stdin: ChildStdin,
}

pub struct Daemon {
    paths: LocalPaths,
    token: String,
    state: Mutex<StateSnapshot>,
    subscribers: Mutex<HashMap<u64, mpsc::Sender<String>>>,
    next_subscriber: AtomicU64,
    supervisor: Mutex<Option<SupervisorProcess>>,
    supervisor_waiters: Mutex<HashMap<String, mpsc::Sender<Value>>>,
    next_internal_request: AtomicU64,
    host_processes: Option<Arc<HostProcessManager>>,
    managed_runtime: Option<Arc<ManagedRuntimeController>>,
    operator_config: Arc<OperatorConfigStore>,
    host_operation_running: AtomicBool,
}

impl Daemon {
    pub fn new(paths: LocalPaths) -> io::Result<Arc<Self>> {
        paths.ensure()?;
        let token = load_or_create_token(&paths.token)?;
        let state = StateSnapshot::load(&paths.state);
        let operator_config = OperatorConfigStore::load(paths.root.join("operator-config.json"))?;
        let managed_bootstrap = ManagedRuntimeBootstrap::discover(&paths)?;
        let host_manifest = match env::var_os("LEMMA_LOCALD_HOST_PACK_MANIFEST") {
            Some(path) if !path.is_empty() => Some(PathBuf::from(path)),
            _ => env::var_os("LEMMA_LOCALD_HOST_PACK_ROOT")
                .filter(|path| !path.is_empty())
                .map(PathBuf::from)
                .map(|pack_root| match managed_bootstrap.as_ref() {
                    Some(runtime) => {
                        native_host_pack::prepare(&paths, &pack_root, runtime.manifest_material())
                    }
                    None => prepare_compatibility_host_manifest(&paths, &pack_root),
                })
                .transpose()?,
        };
        let host_processes = host_manifest
            .map(|path| HostProcessManager::load(&path, paths.root.join("logs")))
            .transpose()?;
        if let Some(manager) = host_processes.as_ref() {
            manager.set_backend_environment(operator_config.backend_environment()?);
        }
        let managed_runtime = host_processes
            .as_ref()
            .and_then(|manager| manager.managed_runtime().cloned())
            .map(|spec| {
                managed_bootstrap
                    .as_ref()
                    .ok_or_else(|| {
                        io::Error::new(
                            io::ErrorKind::NotFound,
                            "host manifest requires managed runtime artifacts",
                        )
                    })?
                    .controller(&paths, spec)
            })
            .transpose()?;
        Ok(Arc::new(Self {
            paths,
            token,
            state: Mutex::new(state),
            subscribers: Mutex::new(HashMap::new()),
            next_subscriber: AtomicU64::new(1),
            supervisor: Mutex::new(None),
            supervisor_waiters: Mutex::new(HashMap::new()),
            next_internal_request: AtomicU64::new(1),
            host_processes,
            managed_runtime,
            operator_config,
            host_operation_running: AtomicBool::new(false),
        }))
    }

    pub fn serve(self: Arc<Self>) -> io::Result<()> {
        let listener = create_listener(&self.paths)?;
        self.write_daemon_log("locald listening")?;
        self.start_host_status_monitor();

        for connection in listener.incoming() {
            match connection {
                Ok(stream) => {
                    let daemon = Arc::clone(&self);
                    thread::spawn(move || {
                        if let Err(error) = daemon.handle_client(stream) {
                            let _ = daemon.write_daemon_log(&format!("client error: {error}"));
                        }
                    });
                }
                Err(error) => self.write_daemon_log(&format!("accept error: {error}"))?,
            }
        }
        Ok(())
    }

    fn start_host_status_monitor(self: &Arc<Self>) {
        if self.host_processes.is_none() {
            return;
        }
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let mut previous = String::new();
            loop {
                let manager = daemon
                    .host_processes
                    .as_ref()
                    .expect("host monitor requires manager");
                let event = manager.status_event(None);
                let current = event.to_string();
                if current != previous {
                    previous = current;
                    daemon.broadcast(event);
                }
                thread::sleep(std::time::Duration::from_secs(1));
            }
        });
    }

    fn handle_client(self: &Arc<Self>, stream: LocalSocketStream) -> io::Result<()> {
        let (receive, mut send) = stream.split();
        let mut reader = BufReader::new(receive);
        let Some(raw_hello) = read_bounded_line(&mut reader)? else {
            return Ok(());
        };
        let hello: Value = serde_json::from_str(&raw_hello).map_err(|error| {
            io::Error::new(
                io::ErrorKind::InvalidData,
                format!("invalid hello: {error}"),
            )
        })?;
        if !authenticate(&hello, &self.token) {
            let denied = error_event("unauthorized", "invalid local control capability", None);
            writeln!(send, "{denied}")?;
            return Ok(());
        }

        let subscriber_id = self.next_subscriber.fetch_add(1, Ordering::Relaxed);
        let (sender, receiver) = mpsc::channel::<String>();
        self.subscribers
            .lock()
            .expect("subscriber lock poisoned")
            .insert(subscriber_id, sender.clone());

        thread::spawn(move || {
            for message in receiver {
                if writeln!(send, "{message}").is_err() {
                    break;
                }
            }
        });

        self.send_direct(
            &sender,
            json!({
                "v": PROTOCOL_VERSION,
                "event": "hello",
                "protocol": PROTOCOL_VERSION,
                "daemon_version": DAEMON_VERSION,
                "pid": std::process::id(),
                "compatibility_supervisor": self.managed_runtime.is_none(),
                "mode": if self.managed_runtime.is_some() {
                    "managed-local"
                } else if self.host_processes.is_some() {
                    "host-packs"
                } else {
                    "compatibility"
                },
                "host_pack_release": self.host_processes.as_ref().map(|manager| manager.release()),
            }),
        );
        self.send_direct(
            &sender,
            self.state.lock().expect("state lock poisoned").event(None),
        );

        while let Some(raw) = read_bounded_line(&mut reader)? {
            let request = match serde_json::from_str::<Value>(&raw) {
                Ok(request @ Value::Object(_)) => request,
                Ok(_) => {
                    self.send_direct(
                        &sender,
                        error_event("bad-input", "expected a JSON object", None),
                    );
                    continue;
                }
                Err(error) => {
                    self.send_direct(
                        &sender,
                        error_event("bad-input", format!("invalid JSON: {error}"), None),
                    );
                    continue;
                }
            };
            if !self.dispatch(request, &sender) {
                break;
            }
        }

        self.subscribers
            .lock()
            .expect("subscriber lock poisoned")
            .remove(&subscriber_id);
        Ok(())
    }

    fn dispatch(self: &Arc<Self>, request: Value, client: &mpsc::Sender<String>) -> bool {
        let command = request
            .get("cmd")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let id = request.get("id").cloned();
        if command == "shutdown-daemon" {
            self.start_daemon_shutdown(id, client.clone());
            return true;
        }
        match command.as_str() {
            "runtime.prepare" => {
                self.start_runtime_prepare(request, client.clone());
                return true;
            }
            "control.snapshot" => {
                match self.control_snapshot(id.as_ref()) {
                    Ok(event) => self.send_direct(client, event),
                    Err(error) => self.send_direct(
                        client,
                        error_event("control-snapshot-failed", error.to_string(), id.as_ref()),
                    ),
                }
                return true;
            }
            "config.apply" => {
                self.apply_operator_config(request, client);
                return true;
            }
            _ => {}
        }
        if let Some(manager) = self.host_processes.as_ref() {
            match command.as_str() {
                "status" => {
                    let mut event = manager.status_event(id.as_ref());
                    let state = self.state.lock().expect("state lock poisoned");
                    event["url"] = Value::String(state.url.clone());
                    event["api_url"] = Value::String(state.api_url.clone());
                    if let Some(runtime) = self.managed_runtime.as_ref() {
                        event["managed_runtime"] =
                            serde_json::to_value(runtime.status()).unwrap_or(Value::Null);
                    }
                    self.send_direct(client, event);
                    return true;
                }
                "start" | "stop" | "restart" => {
                    self.start_host_operation(command, request, client.clone());
                    return true;
                }
                _ => {}
            }
        }
        match command.as_str() {
            "ping" => self.send_direct(
                client,
                json!({"v": PROTOCOL_VERSION, "event": "pong", "id": id.as_ref()}),
            ),
            "status" if !self.supervisor_running() => {
                let event = self
                    .state
                    .lock()
                    .expect("state lock poisoned")
                    .event(id.as_ref());
                self.send_direct(client, event);
            }
            "start" | "stop" | "restart" | "status" => {
                if let Err(error) = self.send_to_supervisor(request) {
                    self.send_direct(
                        client,
                        error_event("supervisor-unavailable", error.to_string(), id.as_ref()),
                    );
                }
            }
            "hello" => self.send_direct(
                client,
                error_event(
                    "already-authenticated",
                    "connection is already authenticated",
                    id.as_ref(),
                ),
            ),
            "disconnect" => {
                self.send_direct(
                    client,
                    json!({"v": PROTOCOL_VERSION, "event": "bye", "id": id.as_ref()}),
                );
                return false;
            }
            _ => self.send_direct(
                client,
                error_event(
                    "unknown-command",
                    format!("unknown command {command:?}"),
                    id.as_ref(),
                ),
            ),
        }
        true
    }

    fn control_snapshot(&self, id: Option<&Value>) -> io::Result<Value> {
        let mut event = json!({
            "v": PROTOCOL_VERSION,
            "event": "control.snapshot",
            "operator": self.operator_config.snapshot()?,
            "state": self.state.lock().expect("state lock poisoned").event(None),
            "services": self.host_processes.as_ref().map(|manager| manager.status()),
            "release": self.host_processes.as_ref().map(|manager| manager.release()),
            "managed_runtime": self.managed_runtime.as_ref().and_then(|runtime| runtime.status()),
            "paths": {
                "locald": &self.paths.root,
                "logs": self.paths.root.join("logs"),
            },
        });
        if let Some(id) = id {
            event["id"] = id.clone();
        }
        Ok(event)
    }

    fn apply_operator_config(self: &Arc<Self>, request: Value, client: &mpsc::Sender<String>) {
        let id = request.get("id").cloned();
        if self
            .host_operation_running
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            self.send_direct(
                client,
                error_event("busy", "another local operation is running", id.as_ref()),
            );
            return;
        }
        let payload = request.get("payload").cloned().unwrap_or(Value::Null);
        let apply: ApplyOperatorConfig = match serde_json::from_value(payload) {
            Ok(apply) => apply,
            Err(error) => {
                self.host_operation_running.store(false, Ordering::Release);
                self.send_direct(
                    client,
                    error_event(
                        "bad-input",
                        format!("invalid config patch: {error}"),
                        id.as_ref(),
                    ),
                );
                return;
            }
        };
        self.send_direct(
            client,
            json!({"v": PROTOCOL_VERSION, "event":"ack", "cmd":"config.apply", "id": id.as_ref()}),
        );
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let previous = daemon.operator_config.capture_state();
            let was_running = daemon
                .host_processes
                .as_ref()
                .is_some_and(|manager| manager.desired_running());
            let result = previous.and_then(|previous| {
                let snapshot = daemon.operator_config.apply(apply)?;
                let activate: io::Result<Value> = (|| {
                    if let Some(manager) = daemon.host_processes.as_ref() {
                        manager.set_backend_environment(
                            daemon.operator_config.backend_environment()?,
                        );
                        if was_running {
                            manager.restart_backend()?;
                        }
                    }
                    Ok(snapshot)
                })();
                match activate {
                    Ok(snapshot) => Ok(snapshot),
                    Err(error) => {
                        let rollback = daemon.operator_config.restore_state(previous).and_then(|_| {
                            if let Some(manager) = daemon.host_processes.as_ref() {
                                manager.set_backend_environment(
                                    daemon.operator_config.backend_environment()?,
                                );
                                if was_running {
                                    manager.start_all()?;
                                }
                            }
                            Ok(())
                        });
                        match rollback {
                            Ok(()) => Err(io::Error::other(format!(
                                "new configuration could not be activated and was rolled back: {error}"
                            ))),
                            Err(rollback_error) => Err(io::Error::other(format!(
                                "new configuration could not be activated: {error}; rollback also failed: {rollback_error}"
                            ))),
                        }
                    }
                }
            });
            match result {
                Ok(snapshot) => daemon.broadcast(json!({
                    "v": PROTOCOL_VERSION,
                    "event": "config.applied",
                    "id": id.as_ref(),
                    "operator": snapshot,
                    "restart": "backend",
                })),
                Err(error) => daemon.broadcast(error_event(
                    "config-apply-failed",
                    error.to_string(),
                    id.as_ref(),
                )),
            }
            daemon
                .host_operation_running
                .store(false, Ordering::Release);
        });
    }

    fn start_daemon_shutdown(self: &Arc<Self>, id: Option<Value>, client: mpsc::Sender<String>) {
        if self.host_operation_running.load(Ordering::Acquire) {
            self.send_direct(
                &client,
                error_event(
                    "busy",
                    "cannot replace the local daemon during an active operation",
                    id.as_ref(),
                ),
            );
            return;
        }
        self.send_direct(
            &client,
            json!({
                "v": PROTOCOL_VERSION, "event": "ack",
                "cmd": "shutdown-daemon", "id": id.as_ref(),
            }),
        );
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let mut failure = None;
            if let Some(manager) = daemon.host_processes.as_ref() {
                if let Err(error) = manager.stop_all() {
                    failure = Some(error.to_string());
                }
            }
            if let Some(runtime) = daemon.managed_runtime.as_ref() {
                if let Err(error) = runtime.shutdown() {
                    failure.get_or_insert_with(|| error.to_string());
                }
            }
            if let Some(mut supervisor) = daemon
                .supervisor
                .lock()
                .expect("supervisor lock poisoned")
                .take()
            {
                let _ = supervisor.child.kill();
                let _ = supervisor.child.wait();
            }
            if let Some(message) = failure {
                daemon.send_direct(
                    &client,
                    error_event("shutdown-failed", message, id.as_ref()),
                );
                return;
            }
            daemon.send_direct(
                &client,
                json!({
                    "v": PROTOCOL_VERSION, "event": "done",
                    "cmd": "shutdown-daemon", "id": id.as_ref(), "ok": true,
                }),
            );
            // Give the authenticated client writer a moment to flush the
            // acknowledgement before ending this dedicated daemon process.
            thread::sleep(std::time::Duration::from_millis(100));
            std::process::exit(0);
        });
    }

    fn start_host_operation(
        self: &Arc<Self>,
        command: String,
        request: Value,
        client: mpsc::Sender<String>,
    ) {
        let id = request.get("id").cloned();
        if self
            .host_operation_running
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            self.send_direct(
                &client,
                error_event("busy", "another local operation is running", id.as_ref()),
            );
            return;
        }
        self.send_direct(
            &client,
            json!({"v": PROTOCOL_VERSION, "event": "ack", "cmd": command, "id": id.as_ref()}),
        );

        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let manager = daemon
                .host_processes
                .as_ref()
                .expect("host operation requires manager");
            let result = match command.as_str() {
                "start" => daemon.start_host_packs(manager),
                "stop" => {
                    let result = manager.stop_all();
                    if result.is_ok() && request.get("infra").and_then(Value::as_bool) == Some(true)
                    {
                        daemon.stop_private_infra()
                    } else {
                        result
                    }
                }
                "restart" => manager
                    .stop_all()
                    .and_then(|_| daemon.start_host_packs(manager)),
                _ => unreachable!(),
            };

            match result {
                Ok(()) => {
                    let stopped_infra = request
                        .get("infra")
                        .and_then(Value::as_bool)
                        .unwrap_or(false);
                    if command == "stop" {
                        daemon.broadcast(json!({
                            "v": PROTOCOL_VERSION, "event": "state", "status": "stopped",
                            "running": false, "ready": false,
                        }));
                        daemon.broadcast(json!({
                            "v": PROTOCOL_VERSION, "event": "stopped",
                            "infra": stopped_infra,
                        }));
                    }
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION, "event": "done", "cmd": command,
                        "id": id.as_ref(), "ok": true,
                    }));
                }
                Err(error) => {
                    let message = error.to_string();
                    daemon.broadcast(error_event(
                        runtime_operation_error_code(&message, "host-operation-failed"),
                        message,
                        id.as_ref(),
                    ));
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION, "event": "done", "cmd": command,
                        "id": id.as_ref(), "ok": false,
                    }));
                }
            }
            daemon
                .host_operation_running
                .store(false, Ordering::Release);
        });
    }

    fn start_runtime_prepare(self: &Arc<Self>, request: Value, client: mpsc::Sender<String>) {
        let id = request.get("id").cloned();
        let Some(runtime) = self.managed_runtime.as_ref().cloned() else {
            self.send_direct(
                &client,
                error_event(
                    "managed-runtime-unavailable",
                    "this installation does not include an app-owned runtime",
                    id.as_ref(),
                ),
            );
            return;
        };
        if self
            .host_operation_running
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            self.send_direct(
                &client,
                error_event("busy", "another local operation is running", id.as_ref()),
            );
            return;
        }
        self.send_direct(
            &client,
            json!({
                "v": PROTOCOL_VERSION,
                "event": "ack",
                "cmd": "runtime.prepare",
                "id": id.as_ref(),
            }),
        );

        let daemon = Arc::clone(self);
        thread::spawn(move || {
            match runtime.prepare_host() {
                Ok(result) => {
                    let mut prepared = json!({
                        "v": PROTOCOL_VERSION,
                        "event": "runtime.prepared",
                        "id": id.as_ref(),
                    });
                    if let (Some(target), Some(fields)) =
                        (prepared.as_object_mut(), result.as_object())
                    {
                        target.extend(fields.clone());
                    }
                    daemon.broadcast(prepared);
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "done",
                        "cmd": "runtime.prepare",
                        "id": id.as_ref(),
                        "ok": true,
                    }));
                }
                Err(error) => {
                    let message = error.to_string();
                    daemon.broadcast(error_event(
                        runtime_operation_error_code(&message, "runtime-prepare-failed"),
                        message,
                        id.as_ref(),
                    ));
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "done",
                        "cmd": "runtime.prepare",
                        "id": id.as_ref(),
                        "ok": false,
                    }));
                }
            }
            daemon
                .host_operation_running
                .store(false, Ordering::Release);
        });
    }

    fn start_host_packs(self: &Arc<Self>, manager: &HostProcessManager) -> io::Result<()> {
        self.prepare_private_infra()?;
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "phase", "key": "migrations",
            "label": "Checking workspace data", "progress": 68,
            "detail": "applying native database migrations",
        }));
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "phase", "key": "backend",
            "label": "Preparing backend", "progress": 82, "detail": "starting host pack",
        }));
        manager.start_all()?;
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "phase", "key": "frontend",
            "label": "Preparing Lemma", "progress": 90, "detail": "host packs healthy",
        }));
        let state = self.state.lock().expect("state lock poisoned").clone();
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "phase", "key": "ready",
            "label": "Lemma is ready", "progress": 100, "detail": "",
        }));
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "state", "status": "running",
            "running": true, "ready": true,
        }));
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "ready", "url": state.url,
            "api_url": state.api_url,
            "mode": if self.managed_runtime.is_some() { "managed-local" } else { "host-packs" },
            "release": manager.release(),
        }));
        Ok(())
    }

    fn prepare_private_infra(self: &Arc<Self>) -> io::Result<()> {
        if let Some(runtime) = self.managed_runtime.as_ref() {
            self.broadcast(json!({
                "v": PROTOCOL_VERSION, "event": "phase", "key": "runtime",
                "label": "Preparing private runtime", "progress": 38,
                "detail": "starting app-owned Linux services",
            }));
            return runtime.start();
        }
        self.wait_for_supervisor(json!({
            "cmd": "start", "setup": false, "rebuild": false, "infra_only": true,
        }))
    }

    fn stop_private_infra(self: &Arc<Self>) -> io::Result<()> {
        if let Some(runtime) = self.managed_runtime.as_ref() {
            return runtime.stop_infrastructure();
        }
        self.wait_for_supervisor(json!({"cmd": "stop", "infra": true}))
    }

    fn wait_for_supervisor(self: &Arc<Self>, mut request: Value) -> io::Result<()> {
        let sequence = self.next_internal_request.fetch_add(1, Ordering::Relaxed);
        let id = format!("locald-internal-{sequence}");
        request["id"] = Value::String(id.clone());
        let (sender, receiver) = mpsc::channel();
        self.supervisor_waiters
            .lock()
            .expect("supervisor waiter lock poisoned")
            .insert(id.clone(), sender);
        if let Err(error) = self.send_to_supervisor(request) {
            self.supervisor_waiters
                .lock()
                .expect("supervisor waiter lock poisoned")
                .remove(&id);
            return Err(error);
        }

        let deadline = std::time::Instant::now() + std::time::Duration::from_secs(20 * 60);
        let result = loop {
            let remaining = deadline.saturating_duration_since(std::time::Instant::now());
            if remaining.is_zero() {
                break Err(io::Error::new(
                    io::ErrorKind::TimedOut,
                    "private infrastructure operation timed out",
                ));
            }
            match receiver.recv_timeout(remaining) {
                Ok(event) if event.get("event").and_then(Value::as_str) == Some("done") => {
                    if event.get("ok").and_then(Value::as_bool) == Some(true) {
                        break Ok(());
                    }
                    break Err(io::Error::other("private infrastructure operation failed"));
                }
                Ok(event) if event.get("event").and_then(Value::as_str) == Some("error") => {
                    break Err(io::Error::other(
                        event
                            .get("message")
                            .and_then(Value::as_str)
                            .unwrap_or("private infrastructure operation failed"),
                    ));
                }
                Ok(_) => continue,
                Err(error) => break Err(io::Error::other(error.to_string())),
            }
        };
        self.supervisor_waiters
            .lock()
            .expect("supervisor waiter lock poisoned")
            .remove(&id);
        result
    }

    fn send_direct(&self, client: &mpsc::Sender<String>, event: Value) {
        let _ = client.send(event.to_string());
    }

    fn supervisor_running(&self) -> bool {
        let mut guard = self.supervisor.lock().expect("supervisor lock poisoned");
        let Some(process) = guard.as_mut() else {
            return false;
        };
        match process.child.try_wait() {
            Ok(None) => true,
            Ok(Some(_)) | Err(_) => {
                *guard = None;
                false
            }
        }
    }

    fn ensure_supervisor(self: &Arc<Self>) -> io::Result<()> {
        if self.supervisor_running() {
            return Ok(());
        }

        let mut command = supervisor_command()?;
        command
            .env("LEMMA_DESKTOP", "1")
            .env(
                "AGENTBOX_PROVIDER",
                env::var("AGENTBOX_PROVIDER").unwrap_or_else(|_| "auto".into()),
            )
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped());

        let mut child = command.spawn()?;
        let stdin = child
            .stdin
            .take()
            .ok_or_else(|| io::Error::other("supervisor stdin was not piped"))?;
        let stdout = child
            .stdout
            .take()
            .ok_or_else(|| io::Error::other("supervisor stdout was not piped"))?;
        let stderr = child
            .stderr
            .take()
            .ok_or_else(|| io::Error::other("supervisor stderr was not piped"))?;

        *self.supervisor.lock().expect("supervisor lock poisoned") =
            Some(SupervisorProcess { child, stdin });

        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let mut reader = BufReader::new(stdout);
            loop {
                match read_bounded_line(&mut reader) {
                    Ok(Some(line)) => match serde_json::from_str::<Value>(&line) {
                        Ok(event) => daemon.handle_supervisor_event(event),
                        Err(_) => daemon.broadcast(json!({
                            "v": PROTOCOL_VERSION,
                            "event": "log",
                            "source": "supervisor-stdout",
                            "line": line,
                        })),
                    },
                    Ok(None) => break,
                    Err(error) => {
                        daemon.broadcast(error_event(
                            "supervisor-protocol",
                            format!("supervisor output failed: {error}"),
                            None,
                        ));
                        break;
                    }
                }
            }
            daemon.supervisor_exited();
        });

        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let mut reader = BufReader::new(stderr);
            while let Ok(Some(line)) = read_bounded_line(&mut reader) {
                daemon.broadcast(json!({
                    "v": PROTOCOL_VERSION,
                    "event": "log",
                    "source": "supervisor-stderr",
                    "line": line,
                }));
            }
        });

        Ok(())
    }

    fn send_to_supervisor(self: &Arc<Self>, request: Value) -> io::Result<()> {
        self.ensure_supervisor()?;
        let mut guard = self.supervisor.lock().expect("supervisor lock poisoned");
        let process = guard
            .as_mut()
            .ok_or_else(|| io::Error::other("supervisor exited during startup"))?;
        writeln!(process.stdin, "{request}")?;
        process.stdin.flush()
    }

    fn supervisor_exited(&self) {
        let status = self
            .supervisor
            .lock()
            .expect("supervisor lock poisoned")
            .take()
            .and_then(|mut process| process.child.wait().ok());
        self.broadcast(error_event(
            "supervisor-exited",
            format!("compatibility supervisor exited ({status:?})"),
            None,
        ));
    }

    fn handle_supervisor_event(&self, event: Value) {
        let internal_id = event
            .get("id")
            .and_then(Value::as_str)
            .filter(|id| id.starts_with("locald-internal-"));
        if let Some(id) = internal_id {
            if let Some(waiter) = self
                .supervisor_waiters
                .lock()
                .expect("supervisor waiter lock poisoned")
                .get(id)
            {
                let _ = waiter.send(event.clone());
            }
            if matches!(
                event.get("event").and_then(Value::as_str),
                Some("ack" | "done" | "error")
            ) {
                return;
            }
        }
        self.broadcast(event);
    }

    fn broadcast(&self, event: Value) {
        let line = event.to_string();
        if let Err(error) = append_bounded_journal(&self.paths.journal, &line) {
            let _ = self.write_daemon_log(&format!("journal error: {error}"));
        }

        {
            let mut state = self.state.lock().expect("state lock poisoned");
            let revision = state.revision;
            state.observe(&event);
            if state.revision != revision {
                let _ = state.persist(&self.paths.state);
            }
        }

        self.subscribers
            .lock()
            .expect("subscriber lock poisoned")
            .retain(|_, subscriber| subscriber.send(line.clone()).is_ok());
    }

    fn write_daemon_log(&self, line: &str) -> io::Result<()> {
        use std::fs::OpenOptions;
        let mut file = OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.paths.log)?;
        writeln!(file, "{line}")
    }
}

fn runtime_operation_error_code(message: &str, fallback: &'static str) -> &'static str {
    if message.contains("restart to finish enabling WSL 2") {
        "wsl-reboot-required"
    } else if message.contains("WSL 2 is required") {
        "wsl-required"
    } else if message.contains("did not approve or complete WSL 2 setup") {
        "wsl-setup-denied"
    } else {
        fallback
    }
}

fn supervisor_command() -> io::Result<Command> {
    let mut command = supervisor_base_command()?;
    command.arg("supervise");
    Ok(command)
}

fn supervisor_base_command() -> io::Result<Command> {
    if let Some(path) = env::var_os("LEMMA_LOCALD_SUPERVISOR_BIN")
        .or_else(|| env::var_os("LEMMA_DESKTOP_SUPERVISOR_BIN"))
        .map(PathBuf::from)
        .filter(|path| path.exists())
    {
        return Ok(Command::new(path));
    }

    if let Ok(executable) = env::current_exe() {
        if let Some(parent) = executable.parent() {
            let sibling = parent.join(if cfg!(windows) {
                "lemma-supervisor.exe"
            } else {
                "lemma-supervisor"
            });
            if sibling.exists() {
                return Ok(Command::new(sibling));
            }
        }
    }

    let root = env::var_os("LEMMA_DESKTOP_RUNTIME_ROOT")
        .map(PathBuf::from)
        .or_else(|| {
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .map(PathBuf::from)
        })
        .ok_or_else(|| io::Error::new(io::ErrorKind::NotFound, "runtime root not found"))?;
    if !root.join("lemma-stack/pyproject.toml").exists() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "no bundled compatibility supervisor or lemma-stack checkout found",
        ));
    }

    let mut command = Command::new("uv");
    command
        .current_dir(root)
        .args(["run", "--project", "lemma-stack", "lemma-stack"]);
    Ok(command)
}

fn prepare_compatibility_host_manifest(
    paths: &LocalPaths,
    pack_root: &std::path::Path,
) -> io::Result<PathBuf> {
    let destination = paths.root.join("host-pack.json");
    let provider = transitional_provider(false);
    let mut command = supervisor_base_command()?;
    command
        .args(["host-manifest", "--pack-root"])
        .arg(pack_root)
        .arg("--output")
        .arg(&destination)
        .args(["--provider", &provider])
        .env("LEMMA_DESKTOP", "1");
    let output = command.output()?;
    if !output.status.success() {
        let detail = String::from_utf8_lossy(&output.stderr);
        return Err(io::Error::other(format!(
            "could not prepare native host pack: {}",
            detail.trim()
        )));
    }
    if !destination.is_file() {
        return Err(io::Error::new(
            io::ErrorKind::NotFound,
            "host manifest renderer did not create its output",
        ));
    }
    Ok(destination)
}

fn transitional_provider(managed_runtime_available: bool) -> String {
    match env::var("AGENTBOX_PROVIDER") {
        Ok(provider)
            if matches!(provider.as_str(), "docker" | "podman")
                || (provider == "lemma_local" && managed_runtime_available) =>
        {
            provider
        }
        _ if managed_runtime_available => "lemma_local".into(),
        _ if Command::new("podman").arg("--version").output().is_ok() => "podman".into(),
        _ if Command::new("docker").arg("--version").output().is_ok() => "docker".into(),
        // The compatibility supervisor can install Podman when neither CLI is
        // present. Render the matching backend profile in advance.
        _ => "podman".into(),
    }
}

fn create_listener(paths: &LocalPaths) -> io::Result<LocalSocketListener> {
    let create = || {
        ListenerOptions::new()
            .name(paths.socket_name()?)
            .create_sync()
    };
    match create() {
        Ok(listener) => Ok(listener),
        #[cfg(unix)]
        Err(error) if error.kind() == io::ErrorKind::AddrInUse => {
            use std::os::unix::fs::FileTypeExt;
            let socket = paths.socket_path();
            let metadata = std::fs::symlink_metadata(&socket)?;
            if !metadata.file_type().is_socket() {
                return Err(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    format!(
                        "refusing to replace non-socket endpoint {}",
                        socket.display()
                    ),
                ));
            }
            if LocalSocketStream::connect(paths.socket_name()?).is_ok() {
                return Err(io::Error::new(
                    io::ErrorKind::AlreadyExists,
                    "lemma-locald is already running",
                ));
            }
            std::fs::remove_file(socket)?;
            create()
        }
        Err(error) => Err(error),
    }
}

#[cfg(test)]
mod tests {
    use super::runtime_operation_error_code;

    #[test]
    fn windows_runtime_errors_have_stable_user_action_codes() {
        assert_eq!(
            runtime_operation_error_code(
                "WSL 2 is required for Lemma's private runtime",
                "host-operation-failed"
            ),
            "wsl-required"
        );
        assert_eq!(
            runtime_operation_error_code(
                "Windows must restart to finish enabling WSL 2",
                "host-operation-failed"
            ),
            "wsl-reboot-required"
        );
        assert_eq!(
            runtime_operation_error_code(
                "Windows did not approve or complete WSL 2 setup",
                "runtime-prepare-failed"
            ),
            "wsl-setup-denied"
        );
        assert_eq!(
            runtime_operation_error_code("database failed", "host-operation-failed"),
            "host-operation-failed"
        );
    }
}
