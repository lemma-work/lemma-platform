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
use crate::host_process::HostProcessManager;
use crate::managed_runtime::{
    ManagedRuntimeBootstrap, ManagedRuntimeController, SANDBOX_IMAGES_UNSUPPORTED,
};
use crate::native_host_pack;
use crate::operator_config::{ApplyOperatorConfig, OperatorConfigStore};
use crate::paths::LocalPaths;
use crate::protocol::{
    append_bounded_journal, authenticate, error_event, load_or_create_token, read_bounded_line,
};
use crate::sharing::{EnableSharingRequest, SharingController, SharingMode, TunnelProvider};
use crate::state::StateSnapshot;
use crate::PROTOCOL_VERSION;

const DAEMON_VERSION: &str = env!("CARGO_PKG_VERSION");
// Bump whenever Desktop must replace a durable daemon even when the public
// app/host-pack release has not changed (for example, a test-build hotfix).
const DAEMON_API_REVISION: u64 = 3;

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
    host_pack_root: Option<String>,
    managed_runtime: Option<Arc<ManagedRuntimeController>>,
    operator_config: Arc<OperatorConfigStore>,
    sharing: Option<Arc<SharingController>>,
    host_operation_running: AtomicBool,
    agent_host: Arc<AgentHostSupervisor>,
    /// State this daemon had to repair before it could start, in the operator's
    /// words rather than serde's. Empty on every healthy launch.
    healed: Vec<String>,
}

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
                state.persist(&paths.state)?;
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
            sharing,
            host_operation_running: AtomicBool::new(false),
            agent_host,
            healed,
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
    fn prime_backend_environment(self: &Arc<Self>) {
        if self.host_processes.is_none() {
            return;
        }
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let Some(manager) = daemon.host_processes.as_ref() else {
                return;
            };
            match daemon.backend_environment() {
                Ok(environment) => manager.set_backend_environment(environment),
                Err(error) => {
                    let _ = daemon
                        .write_daemon_log(&format!("backend environment unavailable: {error}"));
                }
            }
        });
    }

    fn start_agent_host_monitor(self: &Arc<Self>) {
        // Honour what the user last chose rather than starting unconditionally.
        // Turning the Agent Host off has to survive a daemon restart, and an
        // unpaired machine has nothing for it to do.
        if self.agent_host.desired_running() {
            if let Err(error) = self.agent_host.start() {
                let _ = self.write_daemon_log(&format!("Agent Host unavailable: {error}"));
            }
        }
        let daemon = Arc::clone(self);
        thread::spawn(move || loop {
            if let Err(error) = daemon.agent_host.reconcile() {
                let _ = daemon.write_daemon_log(&format!("Agent Host recovery failed: {error}"));
            }
            thread::sleep(std::time::Duration::from_secs(1));
        });
    }

    fn start_host_status_monitor(self: &Arc<Self>) {
        if self.host_processes.is_none() {
            return;
        }
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let mut previous = String::new();
            let mut next_runtime_probe = std::time::Instant::now();
            let mut next_runtime_recovery = std::time::Instant::now();
            let mut runtime_failure_reported = false;
            loop {
                let manager = daemon
                    .host_processes
                    .as_ref()
                    .expect("host monitor requires manager");
                let now = std::time::Instant::now();
                if now >= next_runtime_probe
                    && !daemon.host_operation_running.load(Ordering::Acquire)
                {
                    next_runtime_probe = now + std::time::Duration::from_secs(5);
                    if let Some(runtime) = daemon.managed_runtime.as_ref() {
                        let runtime_expected =
                            runtime.status().is_some() || manager.desired_running();
                        if runtime_expected {
                            match runtime.probe() {
                                Ok(_) => {
                                    manager.mark_dependency_ready();
                                    runtime_failure_reported = false;
                                }
                                Err(error) => {
                                    let message = error.to_string();
                                    manager.mark_dependency_unavailable(message.clone());
                                    if !runtime_failure_reported {
                                        runtime_failure_reported = true;
                                        daemon.broadcast(error_event(
                                            "managed-runtime-lost",
                                            format!(
                                                "Lemma's private runtime stopped unexpectedly: {message}"
                                            ),
                                            None,
                                        ));
                                        daemon.broadcast(json!({
                                            "v": PROTOCOL_VERSION,
                                            "event": "state",
                                            "status": "error",
                                            "running": true,
                                            "ready": false,
                                        }));
                                    }
                                    if manager.desired_running()
                                        && now >= next_runtime_recovery
                                        && daemon
                                            .host_operation_running
                                            .compare_exchange(
                                                false,
                                                true,
                                                Ordering::AcqRel,
                                                Ordering::Acquire,
                                            )
                                            .is_ok()
                                    {
                                        next_runtime_recovery =
                                            now + std::time::Duration::from_secs(15);
                                        manager.mark_dependency_recovering();
                                        daemon.broadcast(json!({
                                            "v": PROTOCOL_VERSION,
                                            "event": "phase",
                                            "key": "runtime-recovery",
                                            "label": "Recovering private runtime",
                                            "progress": 38,
                                            "detail": "restarting app-owned Linux services",
                                        }));
                                        let recovery = Arc::clone(&daemon);
                                        thread::spawn(move || {
                                            let result = recovery.recover_managed_stack();
                                            if let Err(error) = result {
                                                if let Some(manager) =
                                                    recovery.host_processes.as_ref()
                                                {
                                                    manager.mark_dependency_unavailable(
                                                        error.to_string(),
                                                    );
                                                }
                                                recovery.broadcast(error_event(
                                                    "managed-runtime-recovery-failed",
                                                    format!(
                                                        "Could not recover Lemma's private runtime: {error}"
                                                    ),
                                                    None,
                                                ));
                                            }
                                            recovery
                                                .host_operation_running
                                                .store(false, Ordering::Release);
                                        });
                                    }
                                }
                            }
                        }
                    }
                }
                let event = manager.status_event(None);
                let current = event.to_string();
                if current != previous {
                    previous = current;
                    daemon.broadcast(event);
                }
                if let Some(sharing) = daemon.sharing.as_ref() {
                    if let Some(message) = sharing.poll_failure() {
                        if daemon
                            .host_operation_running
                            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
                            .is_ok()
                        {
                            let recovery = Arc::clone(&daemon);
                            let sharing = Arc::clone(sharing);
                            thread::spawn(move || {
                                let result = recovery.disable_sharing_transaction(&sharing);
                                match result {
                                    Ok(()) => {
                                        let (url, api_url) = recovery.canonical_urls();
                                        recovery.broadcast(json!({
                                            "v": PROTOCOL_VERSION,
                                            "event": "sharing.changed",
                                            "reason": "tunnel-exited",
                                            "message": message,
                                            "url": url,
                                            "api_url": api_url,
                                            "sharing": sharing.snapshot(true),
                                        }))
                                    }
                                    Err(error) => recovery.broadcast(scoped_error_event(
                                        "sharing",
                                        "sharing-recovery-failed",
                                        format!(
                                            "The tunnel exited and This computer mode could not be restored: {error}"
                                        ),
                                        None,
                                    )),
                                }
                                recovery
                                    .host_operation_running
                                    .store(false, Ordering::Release);
                            });
                        }
                    }
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
        let desktop_client = hello.get("client").and_then(Value::as_str) == Some("desktop");

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
                "daemon_api_revision": DAEMON_API_REVISION,
                "pid": std::process::id(),
                // Which binary is actually serving this socket, resolved through
                // the filesystem rather than argv. A replaced app bundle keeps
                // running from wherever its executable went — ~/.Trash, in the
                // case this was written for — and the shell has no other way to
                // tell that the daemon answering it is not the one it ships.
                // Same version, same API revision, different build.
                "executable": std::env::current_exe()
                    .and_then(|path| std::fs::canonicalize(&path).or(Ok(path)))
                    .ok()
                    .map(|path| path.to_string_lossy().into_owned()),
                "compatibility_supervisor": self.managed_runtime.is_none(),
                "mode": if self.managed_runtime.is_some() {
                    "managed-local"
                } else if self.host_processes.is_some() {
                    "host-packs"
                } else {
                    "compatibility"
                },
                "host_pack_release": self.host_processes.as_ref().map(|manager| manager.release()),
                "host_pack_root": self.host_pack_root.as_deref(),
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
        if desktop_client {
            self.restore_sharing_after_desktop_disconnect();
        }
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
            "local.reset-data" => {
                self.start_local_data_reset(request, client.clone());
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
            "sharing.snapshot" => {
                match self.sharing.as_ref() {
                    Some(sharing) => self.send_direct(
                        client,
                        json!({
                            "v": PROTOCOL_VERSION,
                            "event": "sharing.snapshot",
                            "id": id.as_ref(),
                            "sharing": sharing.snapshot(true),
                        }),
                    ),
                    None => self.send_direct(
                        client,
                        error_event(
                            "sharing-unavailable",
                            "sharing requires the managed local desktop runtime",
                            id.as_ref(),
                        ),
                    ),
                }
                return true;
            }
            "sharing.preflight" => {
                self.sharing_preflight(request, client);
                return true;
            }
            "sharing.enable" => {
                self.start_sharing_enable(request, client.clone());
                return true;
            }
            "sharing.disable" => {
                self.start_sharing_disable(id, client.clone());
                return true;
            }
            "config.apply" => {
                self.apply_operator_config(request, client);
                return true;
            }
            "config.discover-models" => {
                self.discover_provider_models(request, client);
                return true;
            }
            "config.set-ai" => {
                self.set_ai_profile(request, client);
                return true;
            }
            "desktop.release" => {
                self.release_for_desktop_exit(id.as_ref(), client);
                return true;
            }
            "agent-host.status" => {
                self.send_direct(
                    client,
                    json!({
                        "v": PROTOCOL_VERSION,
                        "event": "agent-host.status",
                        "id": id.as_ref(),
                        "agent_host": self.agent_host.detailed_status(),
                    }),
                );
                return true;
            }
            "agent-host.start" | "agent-host.stop" | "agent-host.restart" | "agent-host.pair"
            | "agent-host.unpair" | "agent-host.refresh" => {
                self.start_agent_host_operation(command, request.clone(), client.clone());
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
                    event["agent_host"] = self.agent_host.status();
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
                let mut event = self
                    .state
                    .lock()
                    .expect("state lock poisoned")
                    .event(id.as_ref());
                event["agent_host"] = self.agent_host.status();
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

    // Desktop calls this on full quit. The daemon deliberately outlives the
    // app, so anything that must not survive the app - an open LAN or public
    // exposure, and the Agent Host - is torn down here rather than at daemon
    // shutdown.
    fn release_for_desktop_exit(&self, id: Option<&Value>, client: &mpsc::Sender<String>) {
        self.send_direct(
            client,
            json!({
                "v": PROTOCOL_VERSION,
                "event": "ack",
                "cmd": "desktop.release",
                "id": id,
            }),
        );
        // Both teardowns run regardless of the other's outcome, and both
        // reasons are reported: a failure to close a public tunnel must never
        // be hidden by a failure to stop the Agent Host, or the reverse.
        let mut failures: Vec<String> = Vec::new();
        // The Agent Host runs while the app is open. Quitting is not the user
        // turning it off, so stop the process but leave the preference alone.
        if let Err(error) = self.agent_host.suspend() {
            failures.push(format!("could not stop the Agent Host: {error}"));
        }
        if let Some(sharing) = self.sharing.as_ref() {
            if let Err(error) = self.disable_sharing_transaction(sharing) {
                // Full Desktop exit must close the exposure even if restoring
                // the app origin failed. It is safer to leave the local stack
                // stopped/misconfigured than to leave a public tunnel alive.
                sharing.force_disable();
                failures.push(format!("could not stop sharing: {error}"));
            }
        }
        let failure = (!failures.is_empty()).then(|| failures.join("; "));
        match failure {
            None => self.send_direct(
                client,
                json!({
                    "v": PROTOCOL_VERSION,
                    "event": "done",
                    "cmd": "desktop.release",
                    "id": id,
                    "ok": true,
                }),
            ),
            Some(error) => self.send_direct(
                client,
                error_event(
                    "desktop-release-failed",
                    format!("could not release local services before desktop exit: {error}"),
                    id,
                ),
            ),
        }
    }

    /// Run an Agent Host lifecycle or pairing command off the client thread.
    ///
    /// `pair` and `unpair` reach the backend, so they can take seconds. The
    /// caller gets an immediate ack and the outcome as a later `done`/`error`,
    /// the same shape every other slow locald operation uses.
    fn start_agent_host_operation(
        self: &Arc<Self>,
        command: String,
        request: Value,
        client: mpsc::Sender<String>,
    ) {
        let id = request.get("id").cloned();
        self.send_direct(
            &client,
            json!({
                "v": PROTOCOL_VERSION,
                "event": "ack",
                "cmd": command,
                "id": id,
            }),
        );
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let text = |key: &str| {
                request
                    .get(key)
                    .and_then(Value::as_str)
                    .unwrap_or_default()
                    .to_string()
            };
            let result = match command.as_str() {
                "agent-host.start" => daemon.agent_host.start(),
                "agent-host.stop" => daemon.agent_host.stop(),
                "agent-host.restart" => daemon.agent_host.restart(),
                "agent-host.pair" => {
                    daemon
                        .agent_host
                        .pair(&text("url"), &text("pairing_code"), &text("name"))
                }
                "agent-host.unpair" => {
                    let target = text("target_id");
                    daemon
                        .agent_host
                        .unpair((!target.is_empty()).then_some(target.as_str()))
                }
                _ => daemon.agent_host.refresh(),
            };
            match result {
                Ok(()) => {
                    let status = daemon.agent_host.detailed_status();
                    daemon.send_direct(
                        &client,
                        json!({
                            "v": PROTOCOL_VERSION,
                            "event": "done",
                            "cmd": command,
                            "id": id,
                            "ok": true,
                            "agent_host": status.clone(),
                        }),
                    );
                    // Every open surface - Local settings, the tray, the
                    // workspace page - shows this state, and only one of them
                    // asked for the change.
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "agent-host.status",
                        "agent_host": status,
                    }));
                }
                Err(error) => daemon.send_direct(
                    &client,
                    error_event(
                        "agent-host-operation-failed",
                        error.to_string(),
                        id.as_ref(),
                    ),
                ),
            }
        });
    }

    fn restore_sharing_after_desktop_disconnect(self: &Arc<Self>) {
        let Some(sharing) = self.sharing.as_ref() else {
            return;
        };
        if sharing.active_mode() == SharingMode::ThisComputer {
            return;
        }
        if self
            .host_operation_running
            .compare_exchange(false, true, Ordering::AcqRel, Ordering::Acquire)
            .is_err()
        {
            return;
        }
        let daemon = Arc::clone(self);
        let sharing = Arc::clone(sharing);
        thread::spawn(move || {
            let result = daemon.disable_sharing_transaction(&sharing);
            match result {
                Ok(()) => {
                    let (url, api_url) = daemon.canonical_urls();
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "sharing.changed",
                        "reason": "desktop-disconnected",
                        "url": url,
                        "api_url": api_url,
                        "sharing": sharing.snapshot(true),
                    }))
                }
                Err(error) => daemon.broadcast(scoped_error_event(
                    "sharing",
                    "sharing-disconnect-cleanup-failed",
                    format!("Desktop disconnected and sharing cleanup failed: {error}"),
                    None,
                )),
            }
            daemon
                .host_operation_running
                .store(false, Ordering::Release);
        });
    }

    fn control_snapshot(&self, id: Option<&Value>) -> io::Result<Value> {
        let mut event = json!({
            "v": PROTOCOL_VERSION,
            "event": "control.snapshot",
            "operator": self.operator_config.snapshot()?,
            "state": self.state.lock().expect("state lock poisoned").event(None),
            "services": self.host_processes.as_ref().map(|manager| manager.status()),
            "capabilities": self.host_processes.as_ref().and_then(|manager| manager.capabilities()),
            "release": self.host_processes.as_ref().map(|manager| manager.release()),
            "managed_runtime": self.managed_runtime.as_ref().and_then(|runtime| runtime.status()),
            "sharing": self.sharing.as_ref().map(|sharing| sharing.snapshot(true)),
            "agent_host": self.agent_host.detailed_status(),
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

    fn sharing_preflight(&self, request: Value, client: &mpsc::Sender<String>) {
        let id = request.get("id");
        let provider = match request
            .get("provider")
            .cloned()
            .map(serde_json::from_value::<TunnelProvider>)
            .transpose()
        {
            Ok(provider) => provider,
            Err(error) => {
                self.send_direct(
                    client,
                    error_event("bad-input", format!("unknown tunnel provider: {error}"), id),
                );
                return;
            }
        };
        match self.sharing.as_ref() {
            Some(sharing) => self.send_direct(
                client,
                json!({
                    "v": PROTOCOL_VERSION,
                    "event": "sharing.preflight",
                    "id": id,
                    "preflight": sharing.preflight(provider),
                    "sharing": sharing.snapshot(false),
                }),
            ),
            None => self.send_direct(
                client,
                error_event(
                    "sharing-unavailable",
                    "sharing requires the managed local desktop runtime",
                    id,
                ),
            ),
        }
    }

    fn start_sharing_enable(self: &Arc<Self>, request: Value, client: mpsc::Sender<String>) {
        let id = request.get("id").cloned();
        let Some(sharing) = self.sharing.as_ref().cloned() else {
            self.send_direct(
                &client,
                error_event(
                    "sharing-unavailable",
                    "sharing requires the managed local desktop runtime",
                    id.as_ref(),
                ),
            );
            return;
        };
        let payload = request.get("payload").cloned().unwrap_or(Value::Null);
        let enable: EnableSharingRequest = match serde_json::from_value(payload) {
            Ok(enable) => enable,
            Err(error) => {
                self.send_direct(
                    &client,
                    error_event(
                        "bad-input",
                        format!("invalid sharing request: {error}"),
                        id.as_ref(),
                    ),
                );
                return;
            }
        };
        let stack_ready = {
            let state = self.state.lock().expect("state lock poisoned");
            state.ready && state.running
        };
        if !stack_ready {
            self.send_direct(
                &client,
                error_event(
                    "sharing-stack-not-ready",
                    "Start Lemma and wait until the local stack is healthy before enabling sharing.",
                    id.as_ref(),
                ),
            );
            return;
        }
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
                "cmd": "sharing.enable",
                "id": id.as_ref(),
            }),
        );
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let (progress_stop, progress_receive) = mpsc::channel::<()>();
            let progress_daemon = Arc::clone(&daemon);
            let progress_sharing = Arc::clone(&sharing);
            let progress_id = id.clone();
            let progress_monitor = thread::spawn(move || loop {
                match progress_receive.recv_timeout(std::time::Duration::from_millis(250)) {
                    Ok(()) | Err(mpsc::RecvTimeoutError::Disconnected) => break,
                    Err(mpsc::RecvTimeoutError::Timeout) => {
                        progress_daemon.broadcast(json!({
                            "v": PROTOCOL_VERSION,
                            "event": "sharing.progress",
                            "id": progress_id.as_ref(),
                            "sharing": progress_sharing.snapshot(false),
                        }));
                    }
                }
            });
            let result = daemon.enable_sharing_transaction(&sharing, &enable);
            let _ = progress_stop.send(());
            let _ = progress_monitor.join();
            match result {
                Ok(()) => {
                    let (url, api_url) = daemon.canonical_urls();
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "sharing.changed",
                        "id": id.as_ref(),
                        "ok": true,
                        "url": url,
                        "api_url": api_url,
                        "sharing": sharing.snapshot(true),
                    }))
                }
                Err(error) => daemon.broadcast(scoped_error_event(
                    "sharing",
                    "sharing-enable-failed",
                    error.to_string(),
                    id.as_ref(),
                )),
            }
            daemon
                .host_operation_running
                .store(false, Ordering::Release);
        });
    }

    fn enable_sharing_transaction(
        &self,
        sharing: &SharingController,
        request: &EnableSharingRequest,
    ) -> io::Result<()> {
        let manager = self
            .host_processes
            .as_ref()
            .ok_or_else(|| io::Error::other("host process manager is unavailable"))?;
        let prepared = sharing.prepare_enable(request)?;
        let previous_backend = manager.service_environment("backend");
        let previous_frontend = manager.service_environment("frontend");
        let (backend, frontend) = sharing_environment(&prepared.origin, prepared.mode);
        manager.replace_service_environment("backend", backend);
        manager.replace_service_environment("frontend", frontend);

        let activate = manager
            .restart_all()
            .and_then(|_| validate_canonical_origin(&prepared.origin));
        if let Err(error) = activate {
            manager.replace_service_environment("backend", previous_backend);
            manager.replace_service_environment("frontend", previous_frontend);
            let rollback = manager.restart_all();
            sharing.rollback_enable(error.to_string());
            self.restore_local_canonical_state()?;
            return match rollback {
                Ok(()) => Err(io::Error::other(format!(
                    "sharing could not be activated and was rolled back: {error}"
                ))),
                Err(rollback_error) => Err(io::Error::other(format!(
                    "sharing could not be activated: {error}; rollback also failed: {rollback_error}"
                ))),
            };
        }

        {
            let mut state = self.state.lock().expect("state lock poisoned");
            state.url = prepared.origin.clone();
            state.api_url = format!("{}/_lemma/api", prepared.origin.trim_end_matches('/'));
            state.persist(&self.paths.state)?;
        }
        if let Err(error) = sharing.commit_enable(request) {
            manager.replace_service_environment("backend", previous_backend);
            manager.replace_service_environment("frontend", previous_frontend);
            let rollback = manager.restart_all();
            sharing.rollback_enable(error.to_string());
            self.restore_local_canonical_state()?;
            return match rollback {
                Ok(()) => Err(io::Error::other(format!(
                    "sharing preferences could not be saved and activation was rolled back: {error}"
                ))),
                Err(rollback_error) => Err(io::Error::other(format!(
                    "sharing preferences could not be saved: {error}; rollback also failed: {rollback_error}"
                ))),
            };
        }
        Ok(())
    }

    fn start_sharing_disable(self: &Arc<Self>, id: Option<Value>, client: mpsc::Sender<String>) {
        let Some(sharing) = self.sharing.as_ref().cloned() else {
            self.send_direct(
                &client,
                error_event(
                    "sharing-unavailable",
                    "sharing requires the managed local desktop runtime",
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
                "cmd": "sharing.disable",
                "id": id.as_ref(),
            }),
        );
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let result = daemon.disable_sharing_transaction(&sharing);
            match result {
                Ok(()) => {
                    let (url, api_url) = daemon.canonical_urls();
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "sharing.changed",
                        "id": id.as_ref(),
                        "ok": true,
                        "url": url,
                        "api_url": api_url,
                        "sharing": sharing.snapshot(true),
                    }))
                }
                Err(error) => daemon.broadcast(scoped_error_event(
                    "sharing",
                    "sharing-disable-failed",
                    error.to_string(),
                    id.as_ref(),
                )),
            }
            daemon
                .host_operation_running
                .store(false, Ordering::Release);
        });
    }

    fn disable_sharing_transaction(&self, sharing: &SharingController) -> io::Result<()> {
        if !sharing.begin_disable()? {
            self.restore_local_canonical_state()?;
            return Ok(());
        }
        let manager = self
            .host_processes
            .as_ref()
            .ok_or_else(|| io::Error::other("host process manager is unavailable"))?;
        let previous_backend = manager.replace_service_environment("backend", HashMap::new());
        let previous_frontend = manager.replace_service_environment("frontend", HashMap::new());
        if let Err(error) = manager.restart_all() {
            manager.replace_service_environment("backend", previous_backend);
            manager.replace_service_environment("frontend", previous_frontend);
            let rollback = manager.restart_all();
            sharing.abort_disable(error.to_string());
            return match rollback {
                Ok(()) => Err(io::Error::other(format!(
                    "This computer mode could not be restored; sharing remains active: {error}"
                ))),
                Err(rollback_error) => Err(io::Error::other(format!(
                    "This computer mode could not be restored: {error}; shared-origin rollback also failed: {rollback_error}"
                ))),
            };
        }
        self.restore_local_canonical_state()?;
        sharing.commit_disable();
        Ok(())
    }

    fn restore_local_canonical_state(&self) -> io::Result<()> {
        let Some(sharing) = self.sharing.as_ref() else {
            return Ok(());
        };
        let mut state = self.state.lock().expect("state lock poisoned");
        state.url = sharing.local_origin().to_owned();
        state.api_url = self
            .host_processes
            .as_ref()
            .and_then(|manager| manager.application_ports())
            .map(|(_, backend_port)| {
                format!(
                    "http://{}:{backend_port}",
                    crate::local_domain::LocalDomain::from_env().frontend_host()
                )
            })
            .unwrap_or_else(|| state.api_url.clone());
        state.persist(&self.paths.state)
    }

    fn canonical_urls(&self) -> (String, String) {
        let state = self.state.lock().expect("state lock poisoned");
        (state.url.clone(), state.api_url.clone())
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
            let result = daemon.write_operator_config(|store| store.apply(apply));
            daemon.finish_config_write(result, id.as_ref());
        });
    }

    /// Persist an operator-config change and restart the backend behind it.
    ///
    /// The write itself differs by caller — a whole configuration from the
    /// settings page, one section from onboarding — but everything around it
    /// is the same and is the part that is easy to get wrong: capture the
    /// previous state, re-render the backend environment, restart, and put the
    /// old configuration back if the restart does not come up.
    fn write_operator_config(
        self: &Arc<Self>,
        write: impl FnOnce(&OperatorConfigStore) -> io::Result<Value>,
    ) -> io::Result<Value> {
        let daemon = self;
        {
            let previous = daemon.operator_config.capture_state();
            let backend_restart_available = daemon
                .host_processes
                .as_ref()
                .is_some_and(|manager| manager.backend_restart_available());
            previous.and_then(|previous| {
                let snapshot = write(daemon.operator_config.as_ref())?;
                let activate: io::Result<Value> = (|| {
                    if let Some(manager) = daemon.host_processes.as_ref() {
                        if backend_restart_available {
                            manager.set_backend_environment(daemon.backend_environment()?);
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
                                if backend_restart_available {
                                    manager.set_backend_environment(daemon.backend_environment()?);
                                    manager.restart_backend()?;
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
            })
        }
    }

    /// Announce the outcome of an operator-config write and release the guard.
    fn finish_config_write(self: &Arc<Self>, result: io::Result<Value>, id: Option<&Value>) {
        match result {
            Ok(snapshot) => self.broadcast(json!({
                "v": PROTOCOL_VERSION,
                "event": "config.applied",
                "id": id,
                "operator": snapshot,
                "restart": "backend",
            })),
            Err(error) => self.broadcast(error_event("config-apply-failed", error.to_string(), id)),
        }
        self.host_operation_running.store(false, Ordering::Release);
    }

    /// Change only the AI profile.
    ///
    /// Onboarding runs in the workspace, on a remote origin, and is trusted
    /// with this one section and nothing else — not sharing, not tunnels, not
    /// the runtime. Keeping that narrow is the reason this is its own command
    /// rather than a `config.apply` with the rest of the configuration echoed
    /// back by the caller.
    fn set_ai_profile(self: &Arc<Self>, request: Value, client: &mpsc::Sender<String>) {
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
        self.send_direct(
            client,
            json!({"v": PROTOCOL_VERSION, "event":"ack", "cmd":"config.set-ai", "id": id.as_ref()}),
        );
        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let result = daemon.write_operator_config(|store| store.set_ai(payload));
            daemon.finish_config_write(result, id.as_ref());
        });
    }

    /// Ask a provider what it can run, without committing to anything.
    ///
    /// `config.apply` already probes, but it probes as one step of a write that
    /// restarts the backend — so the only way to find out a provider's model
    /// names was to guess one, apply, and read the error. That is why both the
    /// onboarding step and Local settings asked people to type model ids from
    /// memory. This is the same probe with no write behind it: connect, list,
    /// then let the user pick before anything is saved.
    ///
    /// Deliberately not guarded by `host_operation_running` — it mutates
    /// nothing, and making a read-only lookup wait behind an unrelated start is
    /// how a model picker ends up feeling broken.
    fn discover_provider_models(self: &Arc<Self>, request: Value, client: &mpsc::Sender<String>) {
        let id = request.get("id").cloned();
        let payload = request.get("payload").cloned().unwrap_or(Value::Null);
        let daemon = Arc::clone(self);
        let client = client.clone();
        thread::spawn(move || {
            match daemon.operator_config.discover_models(payload) {
                Ok(models) => daemon.send_direct(
                    &client,
                    json!({
                        "v": PROTOCOL_VERSION,
                        "event": "config.models",
                        "id": id.as_ref(),
                        "models": models,
                    }),
                ),
                Err(error) => daemon.send_direct(
                    &client,
                    error_event("config-discover-failed", error.to_string(), id.as_ref()),
                ),
            };
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
            if let Some(sharing) = daemon.sharing.as_ref() {
                sharing.force_disable();
            }
            if let Some(manager) = daemon.host_processes.as_ref() {
                if let Err(error) = manager.stop_all() {
                    failure = Some(error.to_string());
                }
            }
            if let Err(error) = daemon.agent_host.stop() {
                failure.get_or_insert_with(|| error.to_string());
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
                error_event(
                    "busy",
                    "Lemma is already working on a local operation; its progress will continue in this client",
                    id.as_ref(),
                ),
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
                "start" => daemon.start_host_packs(manager, id.as_ref()),
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
                    .and_then(|_| daemon.start_host_packs(manager, id.as_ref())),
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
                            "operation_id": id.as_ref(),
                        }));
                        daemon.broadcast(json!({
                            "v": PROTOCOL_VERSION, "event": "stopped",
                            "infra": stopped_infra,
                            "operation_id": id.as_ref(),
                        }));
                    }
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION, "event": "done", "cmd": command,
                        "id": id.as_ref(), "ok": true,
                    }));
                }
                Err(error) => {
                    let message = error.to_string();
                    let mut event = error_event(
                        runtime_operation_error_code(&message, "host-operation-failed"),
                        message.clone(),
                        id.as_ref(),
                    );
                    event["operation_id"] = id.clone().unwrap_or(Value::Null);
                    let (component, log_source) = error_diagnostic_source(&message);
                    event["component"] = Value::String(component.into());
                    event["log_source"] = Value::String(log_source.into());
                    daemon.broadcast(event);
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

    /// Destroy everything on this computer that the user made, then start clean.
    ///
    /// A locald verb rather than something the shell does, because only locald
    /// owns the VM lifecycle -- and because the progress the splash already
    /// renders comes from here. It takes `host_operation_running`, the same
    /// guard `start`, `stop`, `restart` and `runtime.prepare` take, so a reset
    /// can never interleave with a start.
    ///
    /// There is no rollback arm. `repair_runtime` can roll back because a
    /// runtime is replaceable; data is not, and by the time anything here can
    /// fail it is already gone. A failed restart afterwards therefore reports
    /// that plainly and leaves the full-reinstall option on screen, rather than
    /// retrying and pretending.
    fn start_local_data_reset(self: &Arc<Self>, request: Value, client: mpsc::Sender<String>) {
        let id = request.get("id").cloned();
        if request.get("confirm").and_then(Value::as_str) != Some("reset-local-data") {
            self.send_direct(
                &client,
                error_event(
                    "confirmation-required",
                    "a local data reset must be confirmed explicitly",
                    id.as_ref(),
                ),
            );
            return;
        }
        let Some(manager) = self.host_processes.as_ref().cloned() else {
            self.send_direct(
                &client,
                error_event(
                    "host-pack-unavailable",
                    "this installation does not manage local services",
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
                "cmd": "local.reset-data",
                "id": id.as_ref(),
            }),
        );

        let daemon = Arc::clone(self);
        thread::spawn(move || {
            let outcome = daemon.perform_local_data_reset(&manager, id.as_ref());
            match outcome {
                Ok(summary) => {
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "local.data-reset",
                        "id": id.as_ref(),
                        "summary": summary,
                    }));
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "done",
                        "cmd": "local.reset-data",
                        "id": id.as_ref(),
                        "ok": true,
                    }));
                }
                Err(error) => {
                    daemon.broadcast(error_event(
                        "local-data-reset-incomplete",
                        error.to_string(),
                        id.as_ref(),
                    ));
                    daemon.broadcast(json!({
                        "v": PROTOCOL_VERSION,
                        "event": "done",
                        "cmd": "local.reset-data",
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

    /// The reset itself. Order is the safety property.
    fn perform_local_data_reset(
        self: &Arc<Self>,
        manager: &Arc<HostProcessManager>,
        id: Option<&Value>,
    ) -> io::Result<Value> {
        // Stop the things holding the data before removing it. The backend
        // holds Postgres connections and the workspace bind mounts; the Agent
        // Host runs jobs against the workspace it is about to lose.
        manager.stop_all()?;
        // Best effort: an Agent Host that will not stop is not a reason to
        // leave the user stuck with data they cannot use. It is suspended
        // rather than disabled, so it comes back with the clean workspace.
        let _ = self.agent_host.suspend();

        self.broadcast(json!({
            "v": PROTOCOL_VERSION,
            "event": "phase",
            "id": id,
            "key": "reset-data",
            "label": "Erasing local data",
            "detail": "removing databases, files and workspaces on this computer",
            "progress": 20,
        }));

        let summary = self.discard_local_data()?;

        // The database these describe no longer exists. Leaving them would have
        // the next start skip migrations against an empty schema, and the
        // backend would come up against tables that were never created.
        manager.forget_setup_stamps()?;

        // Only once the data is actually gone. A marker cleared before a failed
        // wipe would let the next start run against data it cannot read, which
        // is the state this whole path exists to escape.
        crate::paths::clear_data_reset(&self.paths.root)?;

        self.broadcast(json!({
            "v": PROTOCOL_VERSION,
            "event": "phase",
            "id": id,
            "key": "reset-data",
            "label": "Setting up again",
            "detail": "starting Lemma with a clean workspace",
            "progress": 45,
        }));
        self.start_host_packs(manager, id)?;
        Ok(summary)
    }

    /// Ask the guest to tidy up; discard the whole disk if it cannot.
    ///
    /// Chosen by a precondition rather than by retrying a failure. The surgical
    /// path keeps the pulled container images, which for the case this exists
    /// for -- a Postgres major that moved -- is the difference between seconds
    /// and re-downloading several hundred megabytes.
    fn discard_local_data(&self) -> io::Result<Value> {
        let Some(runtime) = self.managed_runtime.as_ref() else {
            // No guest and no data disk -- but files and object storage are on
            // the host either way, so they still have to go.
            let host_side = crate::paths::discard_host_side_data(&self.paths.root)?;
            return Ok(json!({"strategy": "none", "host_bytes": host_side}));
        };
        // The user's files live on the Mac, not in the guest, so neither
        // strategy below touches them. `LOCAL_FILE_STORAGE_ROOT` and
        // `LOCAL_OBJECT_STORAGE_ROOT` point at `<root>/data/...`, and clearing
        // only the guest erased the rows while leaving every uploaded byte on
        // disk -- under a button whose own text says it "erases your pods,
        // files and accounts". Someone resetting before handing the machine on
        // would have kept all of it.
        let host_side = crate::paths::discard_host_side_data(&self.paths.root)?;
        if runtime.probe().is_ok() {
            let removed = runtime.reset_guest_data()?;
            runtime.stop_infrastructure()?;
            return Ok(json!({
                "strategy": "guest",
                "removed": removed,
                "host_bytes": host_side,
            }));
        }
        #[cfg(target_os = "macos")]
        {
            let reclaimed = runtime.discard_data_disk()?;
            Ok(json!({
                "strategy": "disk",
                "reclaimed_bytes": reclaimed + host_side,
            }))
        }
        // Elsewhere the guest is the only way in: a WSL distribution is
        // unregistered rather than having a disk file to unlink, and that path
        // is not wired up yet.
        #[cfg(not(target_os = "macos"))]
        Err(io::Error::other(
            "the private runtime is not responding, so local data cannot be reset from here",
        ))
    }

    fn start_host_packs(
        self: &Arc<Self>,
        manager: &HostProcessManager,
        operation_id: Option<&Value>,
    ) -> io::Result<()> {
        // Refuse before touching the guest. Something has already replaced a
        // credential this installation's data was written under, so starting
        // would fail deep inside migrations as an opaque auth error, or come up
        // unable to decrypt its own rows. Say so here, in words the shell turns
        // into a reset button.
        if let Some(reason) = crate::paths::data_reset_reason(&self.paths.root) {
            return Err(io::Error::other(format!(
                "{reason}; {}",
                crate::paths::DATA_RESET_MARKER
            )));
        }
        let runtime_generation = manager.prepare_runtime_generation()?;
        self.prepare_private_infra(operation_id, &runtime_generation)?;
        manager.mark_dependency_ready();
        manager.set_backend_environment(self.backend_environment()?);
        manager.start_all_with_progress(|component| {
            let (label, progress, detail, log_source) = match component {
                "migrations" => (
                    "Checking workspace data",
                    68,
                    "applying database migrations and seeding connectors",
                    "migrations",
                ),
                "backend" => (
                    "Starting the Lemma backend",
                    78,
                    "starting API, workers, schedules, the sandbox runtime, and document processing",
                    "backend",
                ),
                "frontend" => (
                    "Starting the Lemma interface",
                    90,
                    "starting the local Next.js application",
                    "frontend",
                ),
                _ => ("Starting Lemma", 75, "starting a host component", "locald"),
            };
            self.broadcast(json!({
                "v": PROTOCOL_VERSION,
                "event": "phase",
                "key": component,
                "stage": component,
                "label": label,
                "progress": progress,
                "detail": detail,
                "current": 0,
                "total": 1,
                "bytes": false,
                "operation_id": operation_id,
                "runtime_generation": runtime_generation,
                "component": component,
                "log_source": log_source,
            }));
        })?;
        // The auth service was started before the backend and is only now
        // waited for, so it came up alongside it rather than in front of it.
        // Nothing may report ready until it answers: a workspace whose first
        // act is signing in would otherwise meet an auth service that is not
        // there yet, which is worse than the wait this removes.
        //
        // Its failure has to take the host processes with it. They are running
        // by this point -- that is the whole point -- and a stack with no auth
        // is not a stack anybody can use.
        if let Some(runtime) = self.managed_runtime.as_ref() {
            if let Err(error) = runtime.await_private_services() {
                if let Some(manager) = self.host_processes.as_ref() {
                    let _ = manager.stop_all();
                }
                return Err(error);
            }
        }
        let state = self.state.lock().expect("state lock poisoned").clone();
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "phase", "key": "ready",
            "label": "Lemma is ready", "progress": 100, "detail": "",
            "operation_id": operation_id,
            "runtime_generation": runtime_generation,
        }));
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "state", "status": "running",
            "running": true, "ready": true,
            "operation_id": operation_id,
            "runtime_generation": runtime_generation,
        }));
        self.broadcast(json!({
            "v": PROTOCOL_VERSION, "event": "ready", "url": state.url,
            "api_url": state.api_url,
            "mode": if self.managed_runtime.is_some() { "managed-local" } else { "host-packs" },
            "release": manager.release(),
            "capabilities": manager.capabilities(),
            "operation_id": operation_id,
            "runtime_generation": runtime_generation,
        }));
        self.warm_sandbox_images();
        Ok(())
    }

    /// Start fetching the sandbox images behind the workspace, and say so.
    ///
    /// After `ready`, never before it. The images are only needed once a pod
    /// runs something; fetching them inline held the startup bar at 68% behind
    /// several hundred megabytes on a first run. The app shows this as a
    /// notice it can take away again, rather than as a phase of starting.
    fn warm_sandbox_images(self: &Arc<Self>) {
        let Some(runtime) = self.managed_runtime.as_ref() else {
            // No guest to warm -- this is a supervisor-mode stack. Said out
            // loud, and terminally, because the workspace polls until it hears
            // an answer that cannot change; silence here left it asking every
            // two seconds for the rest of the session.
            self.broadcast(json!({
                "v": PROTOCOL_VERSION,
                "event": "sandbox-images",
                "state": SANDBOX_IMAGES_UNSUPPORTED,
                "detail": "",
            }));
            return;
        };
        let daemon = Arc::clone(self);
        runtime.warm_sandbox_images(move |status| {
            daemon.broadcast(json!({
                "v": PROTOCOL_VERSION,
                "event": "sandbox-images",
                "state": status.state,
                "detail": status.detail,
            }));
        });
    }

    fn recover_managed_stack(self: &Arc<Self>) -> io::Result<()> {
        let runtime = self
            .managed_runtime
            .as_ref()
            .ok_or_else(|| io::Error::other("managed runtime is unavailable"))?;
        let manager = self
            .host_processes
            .as_ref()
            .ok_or_else(|| io::Error::other("host process manager is unavailable"))?;
        runtime.start()?;
        manager.set_backend_environment(self.backend_environment()?);
        manager.restart_all()?;
        manager.mark_dependency_ready();

        let state = self.state.lock().expect("state lock poisoned").clone();
        self.broadcast(json!({
            "v": PROTOCOL_VERSION,
            "event": "state",
            "status": "running",
            "running": true,
            "ready": true,
        }));
        self.broadcast(json!({
            "v": PROTOCOL_VERSION,
            "event": "ready",
            "url": state.url,
            "api_url": state.api_url,
            "mode": "managed-local",
            "release": manager.release(),
        }));
        self.warm_sandbox_images();
        Ok(())
    }

    fn backend_environment(&self) -> io::Result<HashMap<String, String>> {
        let operator = self.operator_config.backend_environment()?;
        let infrastructure = self
            .managed_runtime
            .as_ref()
            .map(|runtime| runtime.backend_environment())
            .transpose()?;
        Ok(compose_backend_environment(operator, infrastructure))
    }

    fn prepare_private_infra(
        self: &Arc<Self>,
        operation_id: Option<&Value>,
        runtime_generation: &str,
    ) -> io::Result<()> {
        if let Some(runtime) = self.managed_runtime.as_ref() {
            return runtime.start_with_progress(|component, label, progress, detail| {
                self.broadcast(json!({
                    "v": PROTOCOL_VERSION,
                    "event": "phase",
                    "key": component,
                    "stage": component,
                    "label": label,
                    "progress": progress,
                    "detail": detail,
                    "current": 0,
                    "total": 1,
                    "bytes": false,
                    "operation_id": operation_id,
                    "runtime_generation": runtime_generation,
                    "component": component,
                    "log_source": if component == "vm" { "vm" } else { "guest" },
                }));
            });
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
            // Handed straight to the bundled supervisor, which ships in the
            // same release as this daemon, so both ends move together and no
            // compatibility name is needed here.
            .env(
                "LEMMA_CONTAINER_RUNTIME",
                env::var("LEMMA_CONTAINER_RUNTIME").unwrap_or_else(|_| "auto".into()),
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
            .retain(|_, subscriber| subscriber.send(line.clone()).is_ok());
    }

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

fn compose_backend_environment(
    mut operator: HashMap<String, String>,
    infrastructure: Option<HashMap<String, String>>,
) -> HashMap<String, String> {
    if let Some(infrastructure) = infrastructure {
        // Infrastructure endpoints describe the currently running private
        // runtime and must win over the static loopback defaults rendered into
        // the host pack. Reapplying operator configuration must never discard
        // these addresses.
        operator.extend(infrastructure);
    }
    operator
}

fn sharing_environment(
    origin: &str,
    mode: SharingMode,
) -> (HashMap<String, String>, HashMap<String, String>) {
    let origin = origin.trim_end_matches('/');
    let api_url = format!("{origin}/_lemma/api");
    let auth_url = format!("{origin}/auth");
    let secure = mode == SharingMode::Public;
    let exact_origin = exact_origin_regex(origin);
    let backend = HashMap::from([
        ("API_URL".into(), api_url.clone()),
        ("FRONTEND_URL".into(), origin.into()),
        ("AUTH_FRONTEND_URL".into(), auth_url.clone()),
        ("AUTH_WEBSITE_BASE_PATH".into(), "/auth".into()),
        ("SUPERTOKENS_API_BASE_PATH".into(), "/auth".into()),
        (
            "SUPERTOKENS_API_GATEWAY_PATH".into(),
            "/_lemma/api/st".into(),
        ),
        (
            "SESSION_COOKIE_SECURE".into(),
            if secure { "true" } else { "false" }.into(),
        ),
        ("SESSION_COOKIE_SAME_SITE".into(), "lax".into()),
        ("SESSION_COOKIE_DOMAIN".into(), String::new()),
        // No app host is served through a tunnel, so stop claiming one.
        //
        // The gateway routes by *path* -- `/_lemma/api` to the backend, the rest
        // to the frontend -- so there is no host-based route for
        // `<slug>.apps.<domain>` and there cannot be one without wildcard DNS on
        // the tunnel. Left set, `public_app_url` kept handing visitors
        // `<slug>.apps.lemma.localhost`, which their browser resolves against
        // *their own* machine: not a dead link but one pointing somewhere else
        // entirely. Blank makes `public_app_url` return None and
        // `app_slug_from_host` decline to route, which is the truth.
        ("APP_BASE_DOMAIN".into(), String::new()),
        // ...and with no app origin, the app-origin API door is meaningless.
        // It aliases the whole API under `/_lemma` on whatever origin serves
        // user-authored HTML, and widens the refresh cookie to `Path=/` to make
        // that work. Neither is wanted on a public origin.
        ("APP_API_VIA_APP_ORIGIN".into(), "false".into()),
        ("AUTH_EMAIL_VERIFICATION_REQUIRED".into(), "false".into()),
        ("CORS_ORIGIN_REGEX".into(), exact_origin),
        // Raised, not merely rewritten.
        //
        // The host pack turns every abuse control off, which is right for an
        // installation only this Mac can reach. This overlay is applied when
        // that stops being true -- and it used to change URLs and nothing else,
        // so an installation reachable from the LAN or the open internet still
        // had no rate limit on sign-in, no ceiling on account creation, and no
        // ALTCHA. Anyone who found the address got unlimited, unthrottled
        // password guessing against the owner's account.
        //
        // `DEBUG` matters for the same reason: the backend's own config
        // validator explains that it makes every unhandled error answer with a
        // source-annotated traceback, and it is set unconditionally for local
        // mode.
        ("AUTH_ABUSE_PROTECTION_ENABLED".into(), "true".into()),
        ("AUTH_ALTCHA_ENABLED".into(), "true".into()),
        ("DEBUG".into(), "false".into()),
    ]);
    let frontend = HashMap::from([
        ("NEXT_PUBLIC_API_URL".into(), api_url),
        ("NEXT_PUBLIC_AUTH_URL".into(), auth_url),
        ("NEXT_PUBLIC_SITE_URL".into(), origin.into()),
        ("NEXT_PUBLIC_AUTH_WEBSITE_BASE_PATH".into(), "/auth".into()),
        (
            "NEXT_PUBLIC_SUPERTOKENS_API_BASE_PATH".into(),
            "/auth".into(),
        ),
        (
            "NEXT_PUBLIC_SUPERTOKENS_API_GATEWAY_PATH".into(),
            "/_lemma/api/st".into(),
        ),
        (
            "NEXT_PUBLIC_AUTH_DEFAULT_REDIRECT_URI".into(),
            format!("{origin}/"),
        ),
        ("NEXT_PUBLIC_SESSION_TOKEN_DOMAIN".into(), String::new()),
        (
            "NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED".into(),
            "false".into(),
        ),
    ]);
    (backend, frontend)
}

fn exact_origin_regex(origin: &str) -> String {
    let mut escaped = String::with_capacity(origin.len() + 2);
    escaped.push('^');
    for character in origin.chars() {
        if matches!(
            character,
            '.' | '+' | '*' | '?' | '^' | '$' | '(' | ')' | '[' | ']' | '{' | '}' | '|' | '\\'
        ) {
            escaped.push('\\');
        }
        escaped.push(character);
    }
    escaped.push('$');
    escaped
}

fn validate_canonical_origin(origin: &str) -> io::Result<()> {
    // no_proxy, like every other client in this crate. locald talks to the
    // stack it is itself supervising, and a proxy configured without a
    // `<local>` bypass would route that at something that has never heard of
    // it. This used to be true for free: before the desktop workspace, locald's
    // reqwest had no system-proxy feature to honour. Sharing one dependency
    // graph with the agent host and the shell means it does now, so the
    // intent has to be written down.
    let client = reqwest::blocking::Client::builder()
        .timeout(std::time::Duration::from_secs(5))
        .no_proxy()
        .build()
        .map_err(io::Error::other)?;
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(45);
    let target = format!("{}/runtime-config.js", origin.trim_end_matches('/'));
    let mut last_error = String::new();
    while std::time::Instant::now() < deadline {
        match client.get(&target).send() {
            Ok(response) if response.status().is_success() => return Ok(()),
            Ok(response) => last_error = format!("HTTP {}", response.status()),
            Err(error) => last_error = error.to_string(),
        }
        thread::sleep(std::time::Duration::from_millis(500));
    }
    Err(io::Error::new(
        io::ErrorKind::TimedOut,
        format!("the shared canonical origin did not become healthy: {last_error}"),
    ))
}

fn runtime_operation_error_code(message: &str, fallback: &'static str) -> &'static str {
    if message.contains("restart to finish enabling WSL 2") {
        "wsl-reboot-required"
    } else if message.contains("WSL 2 is required") {
        "wsl-required"
    } else if message.contains("did not approve or complete WSL 2 setup") {
        "wsl-setup-denied"
    } else if message.contains(crate::paths::DATA_RESET_MARKER) {
        // One phrase, one code, however many detectors raise it. Anything the
        // user cannot fix by retrying but can fix by discarding local data says
        // the marker phrase and lands here.
        "local-data-incompatible"
    } else {
        fallback
    }
}

fn error_diagnostic_source(message: &str) -> (&'static str, &'static str) {
    let message = message.to_ascii_lowercase();
    if message.contains("migration") || message.contains("alembic") {
        ("migrations", "migrations")
    } else if message.contains("frontend") || message.contains("eaddrinuse") {
        ("frontend", "frontend")
    } else if message.contains("backend") || message.contains("health gate") {
        ("backend", "backend")
    } else if message.contains("runtime")
        || message.contains("container")
        || message.contains("registry")
        || message.contains("guest")
    {
        ("infrastructure", "infrastructure")
    } else {
        ("locald", "events")
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
        let mut command = Command::new(path);
        command.no_console_window();
        return Ok(command);
    }

    if let Ok(executable) = env::current_exe() {
        if let Some(parent) = executable.parent() {
            let sibling = parent.join(if cfg!(windows) {
                "lemma-supervisor.exe"
            } else {
                "lemma-supervisor"
            });
            if sibling.exists() {
                let mut command = Command::new(sibling);
                command.no_console_window();
                return Ok(command);
            }
        }
    }

    let root = env::var_os("LEMMA_DESKTOP_RUNTIME_ROOT")
        .map(PathBuf::from)
        .or_else(|| {
            // desktop/locald -> desktop -> the repo checkout. Two levels, not
            // one, since this crate moved under desktop/ with the rest of the
            // native stack.
            PathBuf::from(env!("CARGO_MANIFEST_DIR"))
                .parent()
                .and_then(Path::parent)
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
    command.no_console_window().current_dir(root).args([
        "run",
        "--project",
        "lemma-stack",
        "lemma-stack",
    ]);
    Ok(command)
}

fn prepare_compatibility_host_manifest(
    paths: &LocalPaths,
    pack_root: &std::path::Path,
) -> io::Result<PathBuf> {
    // Dev/compatibility daemons are often terminated with the Tauri process.
    // Reclaim only the exact prior installation processes recorded in locald's
    // verified ledger before rendering a new manifest, matching the packaged
    // managed-runtime path. Otherwise an orphaned Next server can retain the
    // fixed compatibility port and make every later launch fail at 80–90%.
    crate::host_process::reclaim_persisted_installation_processes(&paths.root)?;
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
    match env::var("LEMMA_CONTAINER_RUNTIME") {
        Ok(provider)
            if matches!(provider.as_str(), "docker" | "podman")
                || (provider == "lemma_local" && managed_runtime_available) =>
        {
            provider
        }
        _ if managed_runtime_available => "lemma_local".into(),
        _ if Command::new("podman")
            .no_console_window()
            .arg("--version")
            .output()
            .is_ok() =>
        {
            "podman".into()
        }
        _ if Command::new("docker")
            .no_console_window()
            .arg("--version")
            .output()
            .is_ok() =>
        {
            "docker".into()
        }
        // The compatibility supervisor can install Podman when neither CLI is
        // present. Render the matching backend profile in advance.
        _ => "podman".into(),
    }
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
mod tests {
    use std::collections::HashMap;

    use tempfile::tempdir;

    use super::{
        compose_backend_environment, error_diagnostic_source, exact_origin_regex,
        runtime_operation_error_code, sharing_environment,
    };
    use crate::sharing::SharingMode;

    #[test]
    fn operator_updates_preserve_private_runtime_endpoints() {
        let environment = compose_backend_environment(
            HashMap::from([
                ("LEMMA_OPENAI_API_KEY".into(), "vault-secret".into()),
                (
                    "DATABASE_URL".into(),
                    "postgresql://127.0.0.1:55432/lemma".into(),
                ),
            ]),
            Some(HashMap::from([
                (
                    "DATABASE_URL".into(),
                    "postgresql://192.168.64.37:5432/lemma".into(),
                ),
                ("REDIS_URL".into(), "redis://192.168.64.37:6379".into()),
                (
                    "SUPERTOKENS_CORE_URL".into(),
                    "http://192.168.64.37:3567".into(),
                ),
            ])),
        );

        assert_eq!(environment["LEMMA_OPENAI_API_KEY"], "vault-secret");
        assert_eq!(
            environment["DATABASE_URL"],
            "postgresql://192.168.64.37:5432/lemma"
        );
        assert_eq!(environment["REDIS_URL"], "redis://192.168.64.37:6379");
        assert_eq!(
            environment["SUPERTOKENS_CORE_URL"],
            "http://192.168.64.37:3567"
        );
    }

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

    /// Anything that says the marker phrase gets the code the reset button
    /// keys on -- however many different detectors end up raising it.
    #[test]
    fn stranded_local_data_is_reported_with_the_code_the_reset_button_uses() {
        assert_eq!(
            runtime_operation_error_code(
                "this installation's secret was replaced, and anything encrypted with the \
                 previous one can no longer be read; local data must be reset",
                "host-operation-failed"
            ),
            "local-data-incompatible"
        );
        // The phrase is the whole contract, so a detector nobody has written
        // yet gets the same treatment for free.
        assert_eq!(
            runtime_operation_error_code(
                &format!(
                    "the workspace database was created by PostgreSQL 16 and this release \
                     runs PostgreSQL 18; {}",
                    crate::paths::DATA_RESET_MARKER
                ),
                "host-operation-failed"
            ),
            "local-data-incompatible"
        );
    }

    /// The marker is checked before the guest is touched.
    ///
    /// Reaching `prepare_private_infra` would boot a VM to discover a failure
    /// already known on disk, and the failure it would then report is an opaque
    /// auth error rather than an offer to reset.
    #[test]
    fn a_recorded_data_reset_requirement_survives_until_it_is_cleared() {
        let root = tempdir().unwrap();
        assert!(crate::paths::data_reset_reason(root.path()).is_none());

        crate::paths::require_data_reset(root.path(), "the passwords were replaced").unwrap();
        let reason = crate::paths::data_reset_reason(root.path()).unwrap();
        assert_eq!(reason, "the passwords were replaced");
        assert_eq!(
            runtime_operation_error_code(
                &format!("{reason}; {}", crate::paths::DATA_RESET_MARKER),
                "host-operation-failed"
            ),
            "local-data-incompatible"
        );

        crate::paths::clear_data_reset(root.path()).unwrap();
        assert!(crate::paths::data_reset_reason(root.path()).is_none());
        // Clearing twice is how a reset that retries behaves; it must not fail.
        crate::paths::clear_data_reset(root.path()).unwrap();
    }

    #[test]
    fn startup_errors_select_the_relevant_diagnostic_log() {
        assert_eq!(
            error_diagnostic_source("frontend failed: EADDRINUSE"),
            ("frontend", "frontend")
        );
        assert_eq!(
            error_diagnostic_source("migrations setup exited"),
            ("migrations", "migrations")
        );
        assert_eq!(
            error_diagnostic_source("registry DNS lookup failed"),
            ("infrastructure", "infrastructure")
        );
    }

    #[test]
    fn public_canonical_environment_uses_one_prefixed_secure_origin() {
        let (backend, frontend) =
            sharing_environment("https://lemma.example.com/", SharingMode::Public);
        assert_eq!(backend["API_URL"], "https://lemma.example.com/_lemma/api");
        // A tunnel serves one origin and no app host, so the deployment must
        // stop advertising one. Left set, every app's URL pointed at
        // `<slug>.apps.lemma.localhost` -- which a visitor's browser resolves
        // against their own machine.
        assert_eq!(backend["APP_BASE_DOMAIN"], "");
        assert_eq!(backend["APP_API_VIA_APP_ORIGIN"], "false");
        assert_eq!(backend["FRONTEND_URL"], "https://lemma.example.com");
        assert_eq!(backend["SUPERTOKENS_API_GATEWAY_PATH"], "/_lemma/api/st");
        assert_eq!(backend["SESSION_COOKIE_SECURE"], "true");
        assert_eq!(backend["AUTH_EMAIL_VERIFICATION_REQUIRED"], "false");
        assert_eq!(
            backend["CORS_ORIGIN_REGEX"],
            "^https://lemma\\.example\\.com$"
        );
        assert_eq!(
            frontend["NEXT_PUBLIC_API_URL"],
            "https://lemma.example.com/_lemma/api"
        );
        assert_eq!(
            frontend["NEXT_PUBLIC_AUTH_URL"],
            "https://lemma.example.com/auth"
        );
        assert_eq!(
            frontend["NEXT_PUBLIC_AUTH_EMAIL_VERIFICATION_REQUIRED"],
            "false"
        );
    }

    #[test]
    fn lan_canonical_environment_keeps_host_only_nonsecure_cookies() {
        let (backend, frontend) =
            sharing_environment("http://192.168.1.20:51234", SharingMode::LocalNetwork);
        assert_eq!(backend["SESSION_COOKIE_SECURE"], "false");
        assert_eq!(backend["SESSION_COOKIE_DOMAIN"], "");
        assert_eq!(frontend["NEXT_PUBLIC_SESSION_TOKEN_DOMAIN"], "");
        assert_eq!(
            exact_origin_regex("http://192.168.1.20:51234"),
            "^http://192\\.168\\.1\\.20:51234$"
        );
    }

    /// Exposing an installation raises its defences, in every mode.
    ///
    /// The host pack turns every abuse control off, which is correct while only
    /// this Mac can reach the stack. This overlay is what runs when that stops
    /// being true, and it used to rewrite URLs and nothing else -- so a
    /// workspace on the LAN or the open internet had no sign-in rate limit, no
    /// ceiling on account creation, no ALTCHA, and answered unhandled errors
    /// with a source-annotated traceback.
    #[test]
    fn sharing_raises_the_abuse_controls_the_local_pack_turns_off() {
        for (origin, mode) in [
            ("https://lemma.example.com", SharingMode::Public),
            ("http://192.168.1.20:51234", SharingMode::LocalNetwork),
        ] {
            let (backend, _) = sharing_environment(origin, mode);
            assert_eq!(
                backend["AUTH_ABUSE_PROTECTION_ENABLED"], "true",
                "{origin} is reachable by someone other than this Mac"
            );
            assert_eq!(backend["AUTH_ALTCHA_ENABLED"], "true", "{origin}");
            assert_eq!(
                backend["DEBUG"], "false",
                "{origin} must not answer strangers with tracebacks"
            );
        }
    }
}
