//! The link worker's half of host execution: Lemma's `op` frames in, an
//! exec-server per workspace, answers out.
//!
//! **One exec-server per workspace**, not one per host. Seatbelt fixes a
//! process's confinement when it starts, and a workspace's root and granted
//! folders are only known when it is opened. One shared exec-server would
//! either have to be confined to the union of every workspace's folders -- so
//! a command in one conversation could write into another's bound project --
//! or be restarted with wider parameters whenever a workspace opened
//! somewhere new, killing every other workspace's running commands. One each
//! costs a process per open workspace, which is cheap, and each is confined
//! to exactly its own root and grants.
//!
//! An exec-server that exits is restarted with backoff and told to open its
//! workspace again. Its processes are gone -- they were its children -- so
//! reads of them answer `process_not_found`, which is the truth.

use std::any::Any;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};
use tokio::sync::{mpsc, oneshot, watch};

use super::paths::{OpenParams, admissible, default_root};
use super::seatbelt::{Confinement, MAX_GRANTS};
use super::wire::{ExecRequest, ExecResponse, OpFailure, kind, method};
use crate::link::protocol::OpBody;

/// The first wait before restarting an exec-server that exited, doubling to
/// `RESTART_MAX`. Reset once one has stayed up for `RESTART_RESET`.
const RESTART_MIN: Duration = Duration::from_millis(250);
const RESTART_MAX: Duration = Duration::from_secs(30);
const RESTART_RESET: Duration = Duration::from_secs(60);
/// How long an op waits for an exec-server that is starting.
const START_WAIT: Duration = Duration::from_secs(15);
/// How long a closing exec-server gets to stop its processes before it is
/// killed. Closing its stdin is what asks it to.
const STOP_GRACE: Duration = Duration::from_secs(3);

/// A started exec-server: its stdio, and whatever keeps it alive. Dropping
/// `guard` kills it.
pub struct Launched {
    pub stdin: Box<dyn AsyncWrite + Send + Unpin>,
    pub stdout: Box<dyn AsyncRead + Send + Unpin>,
    pub guard: Box<dyn Any + Send>,
}

/// What one exec-server is started for.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct LaunchSpec {
    pub root_base: PathBuf,
    pub confinement: Confinement,
}

/// How exec-servers are started. The real one wraps the binary in
/// `sandbox-exec`; tests start one in-process or unconfined.
#[async_trait::async_trait]
pub trait Launcher: Send + Sync + 'static {
    /// Whether this launcher can run anything on this machine at all.
    fn available(&self) -> bool;
    async fn launch(&self, spec: &LaunchSpec) -> std::io::Result<Launched>;
}

/// `lemma-agent-host exec-server` as a child process, under Seatbelt when
/// `sandboxed`.
pub struct ProcessLauncher {
    pub executable: PathBuf,
    /// The Agent Host's data directory, where the environment snapshot is
    /// cached.
    pub data_root: PathBuf,
    /// Unconfined is for tests and for debugging on a machine without
    /// Seatbelt; production is always confined.
    pub sandboxed: bool,
}

#[async_trait::async_trait]
impl Launcher for ProcessLauncher {
    fn available(&self) -> bool {
        !self.sandboxed || super::seatbelt::available()
    }

    async fn launch(&self, spec: &LaunchSpec) -> std::io::Result<Launched> {
        let snapshot = super::env::EnvironmentSnapshot::load_or_take(&self.data_root).await;
        let mut command = if self.sandboxed {
            let mut command = tokio::process::Command::new(super::seatbelt::SANDBOX_EXEC);
            command
                .args(spec.confinement.sandbox_arguments())
                .arg(&self.executable);
            command
        } else {
            tokio::process::Command::new(&self.executable)
        };
        command
            .arg("exec-server")
            .arg("--root-base")
            .arg(&spec.root_base)
            .env_clear()
            .envs(&snapshot.variables)
            .env("HOME", &spec.confinement.home)
            .env("TMPDIR", &spec.confinement.tmp)
            // Its log lines land in the host's log, not on a terminal.
            .env("NO_COLOR", "1")
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .kill_on_drop(true);
        let mut child = command.spawn()?;
        let stdin = child.stdin.take().ok_or_else(|| missing("stdin"))?;
        let stdout = child.stdout.take().ok_or_else(|| missing("stdout"))?;
        if let Some(stderr) = child.stderr.take() {
            // The exec-server logs to stderr; it belongs in the host's log.
            tokio::spawn(async move {
                let mut lines = BufReader::new(stderr).lines();
                while let Ok(Some(line)) = lines.next_line().await {
                    tracing::info!(target: "lemma_agent_host::exec_server", "{line}");
                }
            });
        }
        Ok(Launched {
            stdin: Box::new(stdin),
            stdout: Box::new(stdout),
            guard: Box::new(child),
        })
    }
}

fn missing(stream: &str) -> std::io::Error {
    std::io::Error::other(format!("the exec-server has no {stream}"))
}

/// One live exec-server connection.
struct Connection {
    /// Taken on `stop`: the writer task owns stdin, and ends -- closing it --
    /// once the last sender is gone.
    writer: Mutex<Option<mpsc::UnboundedSender<String>>>,
    pending: Arc<Mutex<HashMap<String, oneshot::Sender<ExecResponse>>>>,
    next_id: AtomicU64,
    closed: watch::Receiver<bool>,
    guard: Mutex<Option<Box<dyn Any + Send>>>,
}

type Pending = Arc<Mutex<HashMap<String, oneshot::Sender<ExecResponse>>>>;

fn drain(pending: &Pending) {
    let waiters: Vec<_> = pending
        .lock()
        .unwrap_or_else(PoisonError::into_inner)
        .drain()
        .collect();
    for (id, waiter) in waiters {
        let _ = waiter.send(ExecResponse::from_outcome(
            id,
            Err(OpFailure::unavailable(
                "the command runner on this computer stopped; try again",
            )),
        ));
    }
}

impl Connection {
    fn start(launched: Launched) -> Arc<Self> {
        let Launched {
            mut stdin,
            stdout,
            guard,
        } = launched;
        let (writer, mut outgoing) = mpsc::unbounded_channel::<String>();
        let pending: Pending = Arc::default();
        let (closed_tx, closed) = watch::channel(false);
        tokio::spawn(async move {
            while let Some(mut line) = outgoing.recv().await {
                line.push('\n');
                if stdin.write_all(line.as_bytes()).await.is_err() || stdin.flush().await.is_err() {
                    break;
                }
            }
            // Dropping stdin here is what tells the exec-server to stop.
        });
        let reader_pending = Arc::clone(&pending);
        tokio::spawn(async move {
            let mut lines = BufReader::new(stdout).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                let answer: ExecResponse = match serde_json::from_str(&line) {
                    Ok(answer) => answer,
                    Err(error) => {
                        tracing::warn!(%error, "ignored an exec-server line that did not parse");
                        continue;
                    }
                };
                let waiter = reader_pending
                    .lock()
                    .unwrap_or_else(PoisonError::into_inner)
                    .remove(&answer.id);
                if let Some(waiter) = waiter {
                    let _ = waiter.send(answer);
                }
            }
            // Closed first, then drained: a request registering after the
            // drain sees the mark and does not wait for an answer.
            closed_tx.send_replace(true);
            drain(&reader_pending);
        });
        Arc::new(Self {
            writer: Mutex::new(Some(writer)),
            pending,
            next_id: AtomicU64::new(1),
            closed,
            guard: Mutex::new(Some(guard)),
        })
    }

    fn is_closed(&self) -> bool {
        *self.closed.borrow()
    }

    async fn wait_closed(&self) {
        let mut closed = self.closed.clone();
        while !*closed.borrow_and_update() {
            if closed.changed().await.is_err() {
                return;
            }
        }
    }

    async fn request(
        &self,
        workspace: &str,
        method: &str,
        params: Value,
        deadline_ms: Option<u64>,
    ) -> Result<Value, OpFailure> {
        let id = self.next_id.fetch_add(1, Ordering::Relaxed).to_string();
        let line = serde_json::to_string(&ExecRequest {
            id: id.clone(),
            workspace: workspace.to_owned(),
            method: method.to_owned(),
            params,
            deadline_ms,
        })
        .map_err(|error| OpFailure::invalid(error.to_string()))?;
        let (answer, answered) = oneshot::channel();
        self.pending
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .insert(id.clone(), answer);
        let sent = self
            .writer
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .as_ref()
            .is_some_and(|writer| writer.send(line).is_ok());
        if self.is_closed() || !sent {
            self.pending
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .remove(&id);
            return Err(OpFailure::unavailable(
                "the command runner on this computer is restarting; try again",
            ));
        }
        answered
            .await
            .map_err(|_| OpFailure::unavailable("the command runner on this computer stopped"))?
            .into_outcome()
    }

    /// Ask it to stop by closing its stdin, and kill it if it has not.
    async fn stop(&self) {
        let guard = self
            .guard
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take();
        self.writer
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .take();
        let _ = tokio::time::timeout(STOP_GRACE, self.wait_closed()).await;
        drop(guard);
    }
}

/// The state of one workspace's exec-server, as ops see it.
#[derive(Clone)]
enum Runner {
    Starting,
    Up {
        connection: Arc<Connection>,
        opened: Value,
    },
    Down(OpFailure),
}

struct HostedWorkspace {
    spec: LaunchSpec,
    runner: watch::Receiver<Runner>,
    supervisor: tokio::task::JoinHandle<()>,
    /// Set when the workspace is closed, so the supervisor stops restarting.
    closing: Arc<AtomicBool>,
}

impl HostedWorkspace {
    async fn current(&self, wait: Duration) -> Runner {
        let mut runner = self.runner.clone();
        let _ = tokio::time::timeout(wait, async {
            while matches!(*runner.borrow_and_update(), Runner::Starting) {
                if runner.changed().await.is_err() {
                    return;
                }
            }
        })
        .await;
        runner.borrow().clone()
    }

    async fn stop(self) {
        self.closing.store(true, Ordering::SeqCst);
        self.supervisor.abort();
        let current = self.runner.borrow().clone();
        if let Runner::Up { connection, .. } = current {
            connection.stop().await;
        }
    }
}

/// Where the relay finds things on this machine.
#[derive(Clone, Debug)]
pub struct RelayPaths {
    /// `~/lemma`: default roots are `c/<date>/<slug>` under it.
    pub root_base: PathBuf,
    pub home: PathBuf,
    pub tmp: PathBuf,
    /// The desktop shell's record of folders the owner bound conversations to.
    pub folders: PathBuf,
}

impl RelayPaths {
    /// This machine's, as the Agent Host sees them.
    pub fn current(folders: PathBuf) -> anyhow::Result<Self> {
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .ok_or_else(|| anyhow::anyhow!("HOME is not set"))?;
        Ok(Self {
            root_base: crate::conversation_directory::workspace_root()?,
            home,
            tmp: std::env::temp_dir(),
            folders,
        })
    }
}

/// Lemma's `op` requests, relayed to per-workspace exec-servers.
pub struct ExecRelay {
    launcher: Arc<dyn Launcher>,
    paths: RelayPaths,
    enabled: AtomicBool,
    workspaces: tokio::sync::Mutex<HashMap<String, HostedWorkspace>>,
}

impl ExecRelay {
    pub fn new(launcher: Arc<dyn Launcher>, paths: RelayPaths) -> Arc<Self> {
        Arc::new(Self {
            launcher,
            paths,
            enabled: AtomicBool::new(false),
            workspaces: tokio::sync::Mutex::new(HashMap::new()),
        })
    }

    #[must_use]
    pub fn enabled(&self) -> bool {
        self.enabled.load(Ordering::SeqCst)
    }

    #[must_use]
    pub fn available(&self) -> bool {
        self.launcher.available()
    }

    /// Turn host execution on or off. Off refuses every op from this moment
    /// and stops every exec-server, and with them every command they run:
    /// the owner turning it off means now.
    pub fn set_enabled(self: &Arc<Self>, enabled: bool) {
        let was = self.enabled.swap(enabled, Ordering::SeqCst);
        if was && !enabled {
            let relay = Arc::clone(self);
            tokio::spawn(async move { relay.close_all().await });
        }
    }

    /// Stop every workspace's exec-server.
    pub async fn close_all(&self) {
        let closing: Vec<_> = self.workspaces.lock().await.drain().collect();
        for (_, hosted) in closing {
            hosted.stop().await;
        }
    }

    /// How many workspaces have an exec-server, for tests and status.
    pub async fn open_workspaces(&self) -> usize {
        self.workspaces.lock().await.len()
    }

    async fn relay(&self, op: OpBody) -> Result<Value, OpFailure> {
        if !self.enabled() {
            return Err(OpFailure::unavailable(
                "running commands on this computer is turned off in Lemma's settings",
            ));
        }
        if !self.available() {
            return Err(OpFailure::unavailable(
                "this computer cannot confine commands, so it does not run them",
            ));
        }
        match op.method.as_str() {
            method::WORKSPACE_OPEN => self.open(op).await,
            method::WORKSPACE_CLOSE => {
                let hosted = self.workspaces.lock().await.remove(&op.workspace);
                if let Some(hosted) = hosted {
                    if let Runner::Up { connection, .. } = hosted.current(Duration::ZERO).await {
                        let _ = connection
                            .request(
                                &op.workspace,
                                method::WORKSPACE_CLOSE,
                                json!({}),
                                Some(5_000),
                            )
                            .await;
                    }
                    hosted.stop().await;
                }
                Ok(json!({}))
            }
            _ => {
                let runner = {
                    let workspaces = self.workspaces.lock().await;
                    let Some(hosted) = workspaces.get(&op.workspace) else {
                        return Err(OpFailure::new(
                            kind::WORKSPACE_NOT_OPEN,
                            format!(
                                "workspace {} is not open on this computer; open it first",
                                op.workspace
                            ),
                        ));
                    };
                    hosted.runner.clone()
                };
                let runner = wait_started(runner, START_WAIT).await;
                match runner {
                    Runner::Up { connection, .. } => {
                        connection
                            .request(&op.workspace, &op.method, op.params, op.deadline_ms)
                            .await
                    }
                    Runner::Down(failure) => Err(failure),
                    Runner::Starting => Err(OpFailure::unavailable(
                        "the command runner on this computer is still starting; try again",
                    )),
                }
            }
        }
    }

    /// Decide the workspace's root and grants, start its exec-server if it
    /// does not have one confined to exactly those, and open it there.
    async fn open(&self, op: OpBody) -> Result<Value, OpFailure> {
        let params: OpenParams = serde_json::from_value(op.params.clone())
            .map_err(|error| OpFailure::invalid(error.to_string()))?;
        let spec = self.launch_spec(&op.workspace, &params)?;
        let mut forwarded = op.params;
        if let Value::Object(fields) = &mut forwarded {
            fields.insert("root_hint".into(), json!(spec.confinement.root));
            fields.insert("grants".into(), json!(spec.confinement.grants));
        }
        let mut workspaces = self.workspaces.lock().await;
        if let Some(hosted) = workspaces.get(&op.workspace) {
            if hosted.spec == spec {
                let runner = hosted.runner.clone();
                drop(workspaces);
                return match wait_started(runner, START_WAIT).await {
                    Runner::Up { opened, .. } => Ok(opened),
                    Runner::Down(failure) => Err(failure),
                    Runner::Starting => Err(OpFailure::unavailable(
                        "the command runner on this computer is still starting; try again",
                    )),
                };
            }
            // Somewhere new: this exec-server is confined to the old folders.
            if let Some(previous) = workspaces.remove(&op.workspace) {
                previous.stop().await;
            }
        }
        let hosted = self.host(op.workspace.clone(), spec, forwarded);
        let runner = hosted.runner.clone();
        workspaces.insert(op.workspace, hosted);
        drop(workspaces);
        match wait_started(runner, START_WAIT).await {
            Runner::Up { opened, .. } => Ok(opened),
            Runner::Down(failure) => Err(failure),
            Runner::Starting => Err(OpFailure::unavailable(
                "the command runner on this computer did not start in time; try again",
            )),
        }
    }

    fn launch_spec(&self, workspace: &str, params: &OpenParams) -> Result<LaunchSpec, OpFailure> {
        let bound = self.bound_folders(params.conversation_id);
        let paths = &self.paths;
        let chosen = params.root_hint.as_deref().and_then(|hint| {
            let admitted = admissible(Path::new(hint), &paths.root_base, &paths.home, &bound);
            if admitted.is_none() {
                // The backend naming a folder is not the owner choosing it.
                tracing::warn!(
                    hint,
                    "ignored a workspace root the owner has not bound; using the conversation's own folder"
                );
            }
            admitted
        });
        let root = if let Some(root) = chosen {
            root
        } else {
            let root = default_root(&paths.root_base, params, workspace)?;
            super::server::create_private_dir(&root)
                .map_err(|error| OpFailure::io(&error, &root))?;
            std::fs::canonicalize(&root).map_err(|error| OpFailure::io(&error, &root))?
        };
        let mut grants = Vec::new();
        for grant in &params.grants {
            match admissible(Path::new(grant), &paths.root_base, &paths.home, &bound) {
                Some(grant) if !grants.contains(&grant) && grant != root => grants.push(grant),
                Some(_) => {}
                None => tracing::warn!(grant, "ignored a grant the owner has not bound"),
            }
        }
        if grants.len() > MAX_GRANTS {
            return Err(OpFailure::invalid(format!(
                "a workspace may be granted at most {MAX_GRANTS} folders"
            )));
        }
        let canonical =
            |path: &Path| std::fs::canonicalize(path).unwrap_or_else(|_| path.to_path_buf());
        Ok(LaunchSpec {
            root_base: paths.root_base.clone(),
            confinement: Confinement {
                root,
                home: canonical(&paths.home),
                tmp: canonical(&paths.tmp),
                grants,
            },
        })
    }

    /// The folders the owner bound on this machine: the conversation's own
    /// binding when it names one.
    fn bound_folders(&self, conversation: Option<uuid::Uuid>) -> Vec<PathBuf> {
        let Some(conversation) = conversation else {
            return Vec::new();
        };
        let bindings = crate::conversation_folders::read_bindings(&self.paths.folders);
        crate::conversation_folders::bound_folder(&bindings, conversation)
            .into_iter()
            .collect()
    }

    /// Start supervising an exec-server for `workspace`.
    fn host(&self, workspace: String, spec: LaunchSpec, open: Value) -> HostedWorkspace {
        let (runner_tx, runner) = watch::channel(Runner::Starting);
        let closing = Arc::new(AtomicBool::new(false));
        let launch_with = Arc::clone(&self.launcher);
        let supervised = spec.clone();
        let stopping = Arc::clone(&closing);
        let supervisor = tokio::spawn(async move {
            let mut backoff = RESTART_MIN;
            while !stopping.load(Ordering::SeqCst) {
                let started = Instant::now();
                match launch_with.launch(&supervised).await {
                    Ok(launched) => {
                        let connection = Connection::start(launched);
                        match connection
                            .request(
                                &workspace,
                                method::WORKSPACE_OPEN,
                                open.clone(),
                                Some(30_000),
                            )
                            .await
                        {
                            Ok(opened) => {
                                runner_tx.send_replace(Runner::Up {
                                    connection: Arc::clone(&connection),
                                    opened,
                                });
                            }
                            Err(failure) if failure.kind != kind::EXEC_SERVER_UNAVAILABLE => {
                                // The open itself was refused; restarting will
                                // not change that.
                                runner_tx.send_replace(Runner::Down(failure));
                                connection.stop().await;
                                return;
                            }
                            Err(failure) => {
                                runner_tx.send_replace(Runner::Down(failure));
                            }
                        }
                        connection.wait_closed().await;
                        if stopping.load(Ordering::SeqCst) {
                            return;
                        }
                        tracing::warn!(workspace, "the exec-server exited; restarting it");
                        runner_tx.send_replace(Runner::Down(OpFailure::unavailable(
                            "the command runner on this computer is restarting; try again",
                        )));
                    }
                    Err(error) => {
                        tracing::error!(%error, workspace, "could not start the exec-server");
                        runner_tx.send_replace(Runner::Down(OpFailure::unavailable(format!(
                            "the command runner on this computer could not start: {error}"
                        ))));
                    }
                }
                if started.elapsed() >= RESTART_RESET {
                    backoff = RESTART_MIN;
                }
                tokio::time::sleep(backoff).await;
                backoff = (backoff * 2).min(RESTART_MAX);
            }
        });
        HostedWorkspace {
            spec,
            runner,
            supervisor,
            closing,
        }
    }
}

async fn wait_started(mut runner: watch::Receiver<Runner>, wait: Duration) -> Runner {
    let _ = tokio::time::timeout(wait, async {
        while matches!(*runner.borrow_and_update(), Runner::Starting) {
            if runner.changed().await.is_err() {
                return;
            }
        }
    })
    .await;
    runner.borrow().clone()
}

#[async_trait::async_trait]
impl crate::link::OpHandler for ExecRelay {
    async fn handle(&self, op: OpBody) -> Result<Value, OpFailure> {
        self.relay(op).await
    }
}

/// An exec-server in this process, joined by in-memory pipes. For tests that
/// want the real server without a binary to spawn.
pub struct InProcessLauncher {
    pub launches: AtomicU64,
    running: Mutex<Vec<tokio::task::AbortHandle>>,
}

impl Default for InProcessLauncher {
    fn default() -> Self {
        Self {
            launches: AtomicU64::new(0),
            running: Mutex::new(Vec::new()),
        }
    }
}

impl InProcessLauncher {
    /// End every exec-server this launcher started, as a crash would.
    pub fn crash_all(&self) {
        for task in self
            .running
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .drain(..)
        {
            task.abort();
        }
    }
}

struct AbortOnDrop(tokio::task::JoinHandle<()>);

impl Drop for AbortOnDrop {
    fn drop(&mut self) {
        self.0.abort();
    }
}

#[async_trait::async_trait]
impl Launcher for InProcessLauncher {
    fn available(&self) -> bool {
        true
    }

    async fn launch(&self, spec: &LaunchSpec) -> std::io::Result<Launched> {
        self.launches.fetch_add(1, Ordering::SeqCst);
        // Two pipes, one each way, so closing the host's end of stdin is an
        // EOF the server sees, as it would be for a real child.
        let (host_writer, server_reader) = tokio::io::duplex(1 << 20);
        let (server_writer, host_reader) = tokio::io::duplex(1 << 20);
        let server = super::server::ExecServer::new(super::server::ServerConfig {
            root_base: spec.root_base.clone(),
            home: spec.confinement.home.clone(),
            tmp: spec.confinement.tmp.clone(),
        });
        let task = tokio::spawn(super::server::serve(server, server_reader, server_writer));
        self.running
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .push(task.abort_handle());
        Ok(Launched {
            stdin: Box::new(host_writer),
            stdout: Box::new(host_reader),
            guard: Box::new(AbortOnDrop(task)),
        })
    }
}
