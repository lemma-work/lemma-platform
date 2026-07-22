use std::collections::HashMap;
use std::env;
use std::io::{self, BufReader, Write};
use std::path::PathBuf;
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;

use interprocess::local_socket::{prelude::*, ListenerOptions};
use serde_json::{json, Value};

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
}

impl Daemon {
    pub fn new(paths: LocalPaths) -> io::Result<Arc<Self>> {
        paths.ensure()?;
        let token = load_or_create_token(&paths.token)?;
        let state = StateSnapshot::load(&paths.state);
        Ok(Arc::new(Self {
            paths,
            token,
            state: Mutex::new(state),
            subscribers: Mutex::new(HashMap::new()),
            next_subscriber: AtomicU64::new(1),
            supervisor: Mutex::new(None),
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
            self.dispatch(request, &sender);
        }

        self.subscribers
            .lock()
            .expect("subscriber lock poisoned")
            .remove(&subscriber_id);
        Ok(())
    }

    fn dispatch(self: &Arc<Self>, request: Value, client: &mpsc::Sender<String>) {
        let command = request
            .get("cmd")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let id = request.get("id").cloned();
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
            _ => self.send_direct(
                client,
                error_event(
                    "unknown-command",
                    format!("unknown command {command:?}"),
                    id.as_ref(),
                ),
            ),
        }
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
                        Ok(event) => daemon.broadcast(event),
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
