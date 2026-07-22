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
    host_operation_running: AtomicBool,
}

impl Daemon {
    pub fn new(paths: LocalPaths) -> io::Result<Arc<Self>> {
        paths.ensure()?;
        let token = load_or_create_token(&paths.token)?;
        let state = StateSnapshot::load(&paths.state);
        let host_processes = env::var_os("LEMMA_LOCALD_HOST_PACK_MANIFEST")
            .map(PathBuf::from)
            .map(|path| HostProcessManager::load(&path, paths.root.join("logs")))
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
            host_operation_running: AtomicBool::new(false),
        }))
    }

    pub fn serve(self: Arc<Self>) -> io::Result<()> {
        let listener = create_listener(&self.paths)?;
        self.write_daemon_log("locald listening")?;

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
                "compatibility_supervisor": true,
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
        if let Some(manager) = self.host_processes.as_ref() {
            match command.as_str() {
                "status" => {
                    let mut event = manager.status_event(id.as_ref());
                    let state = self.state.lock().expect("state lock poisoned");
                    event["url"] = Value::String(state.url.clone());
                    event["api_url"] = Value::String(state.api_url.clone());
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
                    if command == "stop" && !stopped_infra {
                        daemon.broadcast(json!({
                            "v": PROTOCOL_VERSION, "event": "state", "status": "stopped",
                            "running": false, "ready": false,
                        }));
                        daemon.broadcast(json!({
                            "v": PROTOCOL_VERSION, "event": "stopped",
                            "infra": false,
                        }));
                    }
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION, "event": "done", "cmd": command,
                        "id": id.as_ref(), "ok": true,
                    }));
                }
                Err(error) => {
                    daemon.broadcast(error_event(
                        "host-operation-failed",
                        error.to_string(),
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

    fn start_host_packs(self: &Arc<Self>, manager: &HostProcessManager) -> io::Result<()> {
        self.prepare_private_infra()?;
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
            "api_url": state.api_url, "mode": "host-packs", "release": manager.release(),
        }));
        Ok(())
    }

    fn prepare_private_infra(self: &Arc<Self>) -> io::Result<()> {
        self.wait_for_supervisor(json!({
            "cmd": "start", "setup": false, "rebuild": false, "infra_only": true,
        }))
    }

    fn stop_private_infra(self: &Arc<Self>) -> io::Result<()> {
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

fn supervisor_command() -> io::Result<Command> {
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
    command.current_dir(root).args([
        "run",
        "--project",
        "lemma-stack",
        "lemma-stack",
        "supervise",
    ]);
    Ok(command)
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
