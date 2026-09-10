use crate::NoConsoleWindow;
use std::collections::HashMap;
use std::env;
use std::io::{self, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, Command, Stdio};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Arc, Mutex};
use std::thread;

use interprocess::local_socket::{prelude::*, ListenerOptions};
use serde_json::{json, Value};

use crate::agent_host::AgentHostSupervisor;
use crate::config_operations::{ConfigOperation, ConfigOperations};
use crate::host_process::HostProcessManager;
use crate::lifecycle::Lifecycle;
use crate::managed_runtime::{
    ManagedRuntimeBootstrap, ManagedRuntimeController, ProbeOutcome, SANDBOX_IMAGES_UNSUPPORTED,
};
use crate::native_host_pack;
use crate::operator_config::{OperatorConfigStore, OperatorConfigUpdate};
use crate::paths::LocalPaths;
use crate::protocol::{
    append_bounded_journal, authenticate, error_event, load_or_create_token, read_bounded_line,
};
use crate::sharing::{EnableSharingRequest, SharingController, SharingMode, TunnelProvider};
use crate::state::StateSnapshot;
use crate::update_transaction::UpdateTransaction;
use crate::PROTOCOL_VERSION;

const DAEMON_VERSION: &str = env!("CARGO_PKG_VERSION");
// Bump whenever Desktop must replace a durable daemon even when the public
// app/host-pack release has not changed (for example, a test-build hotfix).
const DAEMON_API_REVISION: u64 = 6;

/// Broadcasts held for a subscriber that is not keeping up.
///
/// Deep enough to ride out a client busy rendering a burst of progress events,
/// shallow enough that a client which has stopped reading is noticed rather
/// than carried for ever. See `broadcast`.
const SUBSCRIBER_BACKLOG: usize = 512;

struct SupervisorProcess {
    child: Child,
    stdin: ChildStdin,
}

pub struct Daemon {
    paths: LocalPaths,
    token: String,
    state: Mutex<StateSnapshot>,
    subscribers: Mutex<HashMap<u64, mpsc::SyncSender<String>>>,
    next_subscriber: AtomicU64,
    supervisor: Mutex<Option<SupervisorProcess>>,
    supervisor_waiters: Mutex<HashMap<String, mpsc::Sender<Value>>>,
    next_internal_request: AtomicU64,
    host_processes: Option<Arc<HostProcessManager>>,
    host_pack_root: Option<String>,
    managed_runtime: Option<Arc<ManagedRuntimeController>>,
    operator_config: Arc<OperatorConfigStore>,
    config_operations: Option<ConfigOperations>,
    sharing: Option<Arc<SharingController>>,
    lifecycle: Lifecycle,
    agent_lifecycle: Lifecycle,
    shutdown_running: AtomicBool,
    agent_host: Arc<AgentHostSupervisor>,
    /// State this daemon had to repair before it could start, in the operator's
    /// words rather than serde's. Empty on every healthy launch.
    healed: Vec<String>,
    /// The size and modification time of the binary this daemon started from.
    ///
    /// Measured once, here, and not again. The case it exists for is a Windows
    /// in-place update, which replaces the file at `current_exe()` under a
    /// daemon that is still running -- so a stamp read at handshake time is a
    /// measurement of the build that just replaced this one, handed to a shell
    /// that then adopts the daemon the update was meant to retire. It is also
    /// two metadata reads off every connection, which is two more than a value
    /// that cannot change needs.
    executable_stamp: Option<(u64, u128)>,
}

mod agent_host_ops;
mod config_ops;
mod dispatch;
mod environment;
mod handshake;
mod monitors;
mod reset_ops;
mod sharing_ops;
mod stack_ops;
mod startup_state;
mod supervisor;

use dispatch::{error_diagnostic_source, runtime_operation_error_code};
use environment::{compose_backend_environment, validate_canonical_origin};
// Reachable from the host-pack tests, which compare what the pack switches off
// with what this overlay switches back on. The two lists live in different
// modules and nothing else can put them side by side.
pub(crate) use environment::sharing_environment;
use startup_state::remember_derived_origin;
use supervisor::{executable_stamp, prepare_compatibility_host_manifest};

impl Daemon {
    pub fn new(paths: LocalPaths) -> io::Result<Arc<Self>> {
        paths.ensure()?;
        // What this construction had to repair to get going. Reported by
        // `serve`, never swallowed: replacing a credential or a config behind
        // the operator's back is how a self-heal becomes the next mystery.
        let mut healed: Vec<String> = Vec::new();
        let token = load_or_create_token(&paths.token, &mut healed)?;
        let mut state = StateSnapshot::load(&paths.state);
        let operator_config = OperatorConfigStore::load_reporting(
            paths.root.join("operator-config.json"),
            &mut healed,
        )?;
        let managed_bootstrap = ManagedRuntimeBootstrap::discover(&paths, &mut healed)?;
        let host_pack_root = env::var_os("LEMMA_LOCALD_HOST_PACK_ROOT")
            .filter(|path| !path.is_empty())
            .map(PathBuf::from);
        let host_manifest = match env::var_os("LEMMA_LOCALD_HOST_PACK_MANIFEST") {
            Some(path) if !path.is_empty() => Some(PathBuf::from(path)),
            _ => host_pack_root
                .as_ref()
                .map(|pack_root| match managed_bootstrap.as_ref() {
                    Some(runtime) => native_host_pack::prepare(
                        &paths,
                        pack_root,
                        runtime.manifest_material(),
                        &mut healed,
                    ),
                    None => prepare_compatibility_host_manifest(&paths, pack_root),
                })
                .transpose()?,
        };
        let host_processes = host_manifest
            .map(|path| HostProcessManager::load(&path, paths.root.join("logs")))
            .transpose()?;
        // Nothing here may read the operator's secrets. Everything in `new` runs
        // before `serve` binds the control socket, and a credential vault is
        // entitled to stop and ask the user for authorisation first — which would
        // hold the socket hostage while the desktop shell polls for eight seconds
        // and then gives up with "could not connect to lemma-locald", leaving a
        // blank window sitting behind a dialog nobody has answered yet. The
        // backend environment is primed by `prime_backend_environment` instead.
        if let Some(manager) = host_processes.as_ref() {
            if let Some((frontend_port, backend_port)) = manager.application_ports() {
                // LAN/Public desired state is deliberately not persisted.
                // Every daemon launch starts from the private canonical origin.
                state.url = format!(
                    "http://{}:{frontend_port}",
                    crate::local_domain::LocalDomain::from_env().frontend_host()
                );
                state.api_url = format!(
                    "http://{}:{backend_port}",
                    crate::local_domain::LocalDomain::from_env().frontend_host()
                );
                remember_derived_origin(&state, &paths.state, &mut healed);
            }
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
        let sharing = host_processes
            .as_ref()
            .and_then(|manager| {
                manager
                    .application_ports()
                    .map(|(frontend_port, backend_port)| {
                        SharingController::load(
                            &paths.root,
                            state.url.clone(),
                            frontend_port,
                            backend_port,
                        )
                    })
            })
            .transpose()?;
        let agent_host = Arc::new(AgentHostSupervisor::discover(&paths.root));
        let config_operations = match ConfigOperations::load(
            paths.root.join("config-operations.json"),
        ) {
            Ok(journal) => Some(journal),
            Err(error) => {
                healed.push(format!("settings operation history is unavailable: {error}; settings writes are disabled until it is repaired"));
                None
            }
        };
        // An update that stopped part-way is the one piece of startup state that
        // must reach the operator rather than be absorbed. A record left in a
        // phase that had already moved the database means the version about to
        // start may not be able to read it -- see `update_transaction`.
        // Reported, deliberately, rather than held: nothing in this process
        // performs an update yet, and a stored handle nothing reads is a
        // promise the code does not keep. Whoever adds `update.begin` loads it
        // there. What matters at startup is that an interrupted one is said out
        // loud instead of absorbed.
        match UpdateTransaction::load(paths.root.join("update.json")) {
            Ok(transaction) => {
                if let Some(reason) = transaction.blocking_reason() {
                    healed.push(reason);
                }
            }
            Err(error) => healed.push(format!(
                "the record of an in-flight update could not be read: {error}; \
                 check this installation before updating it again"
            )),
        }
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
            host_pack_root: host_pack_root.map(path_identity),
            managed_runtime,
            operator_config,
            config_operations,
            sharing,
            lifecycle: Lifecycle::default(),
            agent_lifecycle: Lifecycle::default(),
            shutdown_running: AtomicBool::new(false),
            agent_host,
            healed,
            // Before `serve` binds the socket, which is the whole point: after
            // that a Windows installer can replace this file while this
            // process is still answering on it.
            executable_stamp: executable_stamp(),
        }))
    }

    pub fn serve(self: Arc<Self>) -> io::Result<()> {
        let listener = create_listener(&self.paths)?;
        self.write_daemon_log("locald listening")?;
        self.report_healed_state();
        self.prime_backend_environment();
        self.start_host_status_monitor();
        self.start_agent_host_monitor();

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

    /// Fill in the backend environment once the control socket is already up.
    ///
    /// Reading the operator's secrets can block on an OS credential-vault prompt,
    /// which is why this cannot happen during `Daemon::new`. Off the accept path
    /// the cost is invisible: the shell connects, the workspace renders, and the
    /// prompt — if there is one — arrives over a window that already works.
    ///
    /// Failure here is deliberately not fatal. Every caller that starts host
    /// processes rebuilds the environment first and surfaces its own error, so a
    /// vault the user dismissed costs a log line rather than the daemon.
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
        let desktop_client = hello.get("client").and_then(Value::as_str) == Some("desktop");

        let subscriber_id = self.next_subscriber.fetch_add(1, Ordering::Relaxed);
        // Bounded. An unbounded channel meant a client that held its socket
        // open and stopped reading accumulated every broadcast for the life of
        // the daemon -- events reach a megabyte each -- and nothing ever
        // noticed. The depth is generous for a client that is merely slow; one
        // that has genuinely stopped is disconnected below rather than carried.
        let (sender, receiver) = mpsc::sync_channel::<String>(SUBSCRIBER_BACKLOG);
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

        self.send_direct(&sender, self.hello_event());
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
        if desktop_client {
            self.restore_sharing_after_desktop_disconnect();
        }
        Ok(())
    }

    fn send_direct(&self, client: &mpsc::SyncSender<String>, event: Value) {
        let _ = client.send(event.to_string());
    }

    fn broadcast(&self, mut event: Value) {
        if event.get("timestamp_ms").is_none() {
            event["timestamp_ms"] = Value::from(
                std::time::SystemTime::now()
                    .duration_since(std::time::UNIX_EPOCH)
                    .unwrap_or_default()
                    .as_millis() as u64,
            );
        }
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
            .retain(|_, subscriber| match subscriber.try_send(line.clone()) {
                Ok(()) => true,
                // Full means this client has stopped draining. Dropping it ends
                // its reader thread and closes its socket, which is the honest
                // outcome: it is no longer receiving anything either way, and
                // the alternative is holding its backlog for ever.
                Err(mpsc::TrySendError::Full(_) | mpsc::TrySendError::Disconnected(_)) => false,
            });
    }

    /// One implementation, two callers: a running daemon writes through here,
    /// and `lemma-locald serve` writes a construction failure through the same
    /// free function before this type exists at all.
    /// One implementation, two callers: a running daemon writes through here,
    /// and `lemma-locald serve` writes a construction failure through the same
    /// free function before this type exists at all.
    fn write_daemon_log(&self, line: &str) -> io::Result<()> {
        crate::protocol::append_bounded_daemon_log(&self.paths.log, line)
    }

    /// Say out loud what `Daemon::new` had to replace to get this far.
    ///
    /// Broadcast as well as logged: a subscriber that connects later still gets
    /// it from the journal, and the app can surface "your configuration was
    /// reset" instead of the operator discovering it by finding their provider
    /// missing.
    /// Say out loud what `Daemon::new` had to replace to get this far.
    ///
    /// Broadcast as well as logged: a subscriber that connects later still gets
    /// it from the journal, and the app can surface "your configuration was
    /// reset" instead of the operator discovering it by finding their provider
    /// missing.
    fn report_healed_state(&self) {
        if self.healed.is_empty() {
            return;
        }
        for note in &self.healed {
            let _ = self.write_daemon_log(&format!("healed: {note}"));
        }
        self.broadcast(json!({
            "v": PROTOCOL_VERSION,
            "event": "local.healed",
            "notes": self.healed.clone(),
        }));
    }
}

fn scoped_error_event(
    scope: &str,
    code: &str,
    message: impl Into<String>,
    id: Option<&Value>,
) -> Value {
    let mut event = error_event(code, message, id);
    event["scope"] = Value::String(scope.to_owned());
    event
}

fn path_identity(path: PathBuf) -> String {
    std::fs::canonicalize(&path)
        .unwrap_or(path)
        .to_string_lossy()
        .into_owned()
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
        #[cfg(windows)]
        Err(error) if error.kind() == io::ErrorKind::AddrInUse => {
            // A named pipe leaves no remnant to clean up: if the name is taken,
            // a server owns it right now. There is nothing to recover, only
            // something to say plainly -- this recovery was unix-only, so on
            // Windows the raw OS error reached the user instead of the one
            // sentence that explains it.
            Err(io::Error::new(
                io::ErrorKind::AlreadyExists,
                "lemma-locald is already running",
            ))
        }
        Err(error) => Err(error),
    }
}

#[cfg(test)]
mod environment_tests;
#[cfg(test)]
mod tests;
