//! `lemma-agent-host exec-server`: host execution's worker, confined by
//! Seatbelt, speaking JSON lines on stdin and stdout.
//!
//! One request per line, one answer per line, correlated by `id` and answered
//! in whatever order they finish -- a `process.read` long-waiting for output
//! must not hold up a `file.stat` behind it. Nothing but answers is written to
//! stdout; logs go to stderr, which the relay forwards to the host's log.
//!
//! When stdin closes, the parent has gone, and so does everything this
//! process started: a command nobody can reach any more is not left running.

use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncRead, AsyncWrite, AsyncWriteExt, BufReader};
use tokio::sync::mpsc;

use super::files::{self, UPLOAD_TTL, Uploads};
use super::paths::{OpenParams, PathPolicy, default_root};
use super::process::ProcessTable;
use super::wire::{ExecRequest, ExecResponse, OpFailure, kind, method, platform};

/// How often finished processes and abandoned uploads are cleared away.
const HOUSEKEEPING_INTERVAL: Duration = Duration::from_secs(15);

/// Where an exec-server's workspaces live, and what they may reach.
#[derive(Clone, Debug)]
pub struct ServerConfig {
    /// `~/lemma`: default roots go under `c/<date>/<slug>` here.
    pub root_base: PathBuf,
    pub home: PathBuf,
    /// `$TMPDIR`, inside every workspace's path policy.
    pub tmp: PathBuf,
}

impl ServerConfig {
    /// From this process's own environment, as the subcommand runs.
    pub fn from_environment(root_base: PathBuf) -> anyhow::Result<Self> {
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .ok_or_else(|| anyhow::anyhow!("HOME is not set"))?;
        Ok(Self {
            root_base,
            home,
            tmp: std::env::temp_dir(),
        })
    }
}

struct Workspace {
    policy: PathPolicy,
    processes: ProcessTable,
    uploads: Uploads,
}

impl Workspace {
    fn close(&self) {
        self.processes.kill_all();
        self.uploads.abandon_all();
    }
}

/// The workspaces one exec-server has open, and the ops on them.
pub struct ExecServer {
    config: ServerConfig,
    workspaces: Mutex<HashMap<String, Arc<Workspace>>>,
}

impl ExecServer {
    #[must_use]
    pub fn new(config: ServerConfig) -> Arc<Self> {
        Arc::new(Self {
            config,
            workspaces: Mutex::new(HashMap::new()),
        })
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, HashMap<String, Arc<Workspace>>> {
        self.workspaces
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }

    /// One request, bounded by its own deadline.
    pub async fn handle(&self, request: ExecRequest) -> Result<Value, OpFailure> {
        let deadline = request.deadline_ms.map(Duration::from_millis);
        let work = self.dispatch(request);
        match deadline {
            Some(deadline) => tokio::time::timeout(deadline, work)
                .await
                .unwrap_or_else(|_| {
                    Err(OpFailure::new(
                        kind::TIMEOUT,
                        format!("the op did not finish within {deadline:?}"),
                    ))
                }),
            None => work.await,
        }
    }

    async fn dispatch(&self, request: ExecRequest) -> Result<Value, OpFailure> {
        let ExecRequest {
            workspace,
            method: name,
            params,
            ..
        } = request;
        match name.as_str() {
            method::WORKSPACE_OPEN => return self.open(&workspace, params),
            method::WORKSPACE_CLOSE => {
                if let Some(closed) = self.lock().remove(&workspace) {
                    closed.close();
                }
                return Ok(json!({}));
            }
            _ => {}
        }
        let open = self.lock().get(&workspace).cloned().ok_or_else(|| {
            OpFailure::new(
                kind::WORKSPACE_NOT_OPEN,
                format!("workspace {workspace} is not open on this computer; open it first"),
            )
        })?;
        let policy = &open.policy;
        match name.as_str() {
            method::PROCESS_START => open.processes.start(policy, params).await,
            method::PROCESS_READ => open.processes.read(params).await,
            method::PROCESS_INPUT => open.processes.input(params).await,
            method::PROCESS_RESIZE => open.processes.resize(params),
            method::PROCESS_TERMINATE => open.processes.terminate(params).await,
            method::PROCESS_LIST => Ok(open.processes.list()),
            method::FILE_STAT => files::file_stat(policy, params).await,
            method::FILE_LIST => files::file_list(policy, params).await,
            method::FILE_MKDIR => files::file_mkdir(policy, params).await,
            method::FILE_READ => files::file_read(policy, params).await,
            method::FILE_WRITE => files::file_write(policy, &open.uploads, params).await,
            method::FILE_MOVE => files::file_move(policy, params).await,
            method::FILE_DELETE => files::file_delete(policy, params).await,
            method::SECRET_DELIVER => files::secret_deliver(policy, params).await,
            other => Err(OpFailure::invalid(format!("unknown method {other}"))),
        }
    }

    /// Open a workspace, or answer for one already open.
    ///
    /// The root is taken as given when `root_hint` is absolute: the relay that
    /// started this process has already decided the owner allows it (see
    /// `paths::admissible`), and Seatbelt holds this process to the same
    /// folders whatever it is told. Otherwise it is the conversation's default
    /// folder under the root base.
    fn open(&self, workspace: &str, raw: Value) -> Result<Value, OpFailure> {
        let params: OpenParams =
            serde_json::from_value(raw).map_err(|error| OpFailure::invalid(error.to_string()))?;
        let root = match params.root_hint.as_deref().map(Path::new) {
            Some(hint) if hint.is_absolute() => hint.to_path_buf(),
            Some(_) => return Err(OpFailure::invalid("root_hint must be an absolute path")),
            None => default_root(&self.config.root_base, &params, workspace)?,
        };
        create_private_dir(&root).map_err(|error| OpFailure::io(&error, &root))?;
        let grants: Vec<PathBuf> = params.grants.iter().map(PathBuf::from).collect();
        let policy = PathPolicy::new(&root, &self.config.tmp, &grants)
            .map_err(|error| OpFailure::io(&error, &root))?;
        let mut workspaces = self.lock();
        let reopened = workspaces
            .get(workspace)
            .is_some_and(|open| open.policy.root() == policy.root());
        if !reopened {
            let fresh = Arc::new(Workspace {
                policy,
                processes: ProcessTable::default(),
                uploads: Uploads::default(),
            });
            if let Some(replaced) = workspaces.insert(workspace.to_owned(), fresh) {
                replaced.close();
            }
        }
        let root = workspaces[workspace].policy.root().to_path_buf();
        Ok(json!({
            "root": root,
            "home": self.config.home,
            "platform": platform(),
        }))
    }

    /// Clear away finished processes and abandoned uploads.
    pub fn housekeeping(&self) {
        let open: Vec<_> = self.lock().values().cloned().collect();
        for workspace in open {
            workspace.processes.reap();
            workspace.uploads.sweep(UPLOAD_TTL);
        }
    }

    /// Close every workspace, killing what runs in them.
    pub fn shutdown(&self) {
        let open: Vec<_> = self
            .lock()
            .drain()
            .map(|(_, workspace)| workspace)
            .collect();
        for workspace in open {
            workspace.close();
        }
    }
}

/// A folder the owner's user only, and every missing parent.
pub(crate) fn create_private_dir(path: &Path) -> std::io::Result<()> {
    use std::os::unix::fs::DirBuilderExt;
    std::fs::DirBuilder::new()
        .recursive(true)
        .mode(0o700)
        .create(path)
}

/// Serve requests from `reader` until it closes, answering on `writer`.
pub async fn serve<R, W>(server: Arc<ExecServer>, reader: R, mut writer: W)
where
    R: AsyncRead + Unpin,
    W: AsyncWrite + Unpin + Send + 'static,
{
    let (answers, mut outgoing) = mpsc::unbounded_channel::<String>();
    let writing = tokio::spawn(async move {
        while let Some(mut line) = outgoing.recv().await {
            line.push('\n');
            if writer.write_all(line.as_bytes()).await.is_err() || writer.flush().await.is_err() {
                return;
            }
        }
    });
    let housekeeping = {
        let server = Arc::clone(&server);
        tokio::spawn(async move {
            let mut interval = tokio::time::interval(HOUSEKEEPING_INTERVAL);
            loop {
                interval.tick().await;
                server.housekeeping();
            }
        })
    };
    let mut lines = BufReader::new(reader).lines();
    while let Ok(Some(line)) = lines.next_line().await {
        if line.trim().is_empty() {
            continue;
        }
        let request: ExecRequest = match serde_json::from_str(&line) {
            Ok(request) => request,
            Err(error) => {
                // Answerable only if the id can still be read out of it.
                let id = serde_json::from_str::<Value>(&line)
                    .ok()
                    .and_then(|value| value.get("id").and_then(Value::as_str).map(str::to_owned));
                tracing::warn!(%error, "the exec-server received a request it could not read");
                if let Some(id) = id {
                    let answer = ExecResponse::from_outcome(
                        id,
                        Err(OpFailure::invalid(format!("unreadable request: {error}"))),
                    );
                    let _ = answers.send(serde_json::to_string(&answer).unwrap_or_default());
                }
                continue;
            }
        };
        // Opening and closing are answered in order, before the next line is
        // read: an op sent after `workspace.open` must find it open. They are
        // quick; everything else runs beside the rest.
        if matches!(
            request.method.as_str(),
            method::WORKSPACE_OPEN | method::WORKSPACE_CLOSE
        ) {
            let id = request.id.clone();
            let answer = ExecResponse::from_outcome(id, server.handle(request).await);
            let _ = answers.send(serde_json::to_string(&answer).unwrap_or_default());
            continue;
        }
        let server = Arc::clone(&server);
        let answers = answers.clone();
        tokio::spawn(async move {
            let id = request.id.clone();
            let outcome = server.handle(request).await;
            let answer = ExecResponse::from_outcome(id, outcome);
            if let Ok(line) = serde_json::to_string(&answer) {
                let _ = answers.send(line);
            }
        });
    }
    housekeeping.abort();
    server.shutdown();
    drop(answers);
    // Answers already on their way are delivered; ones still being worked on
    // have nobody left to read them.
    let _ = tokio::time::timeout(Duration::from_secs(1), writing).await;
}

/// The subcommand: serve stdin and stdout until stdin closes.
pub async fn run(root_base: PathBuf) -> anyhow::Result<()> {
    let config = ServerConfig::from_environment(root_base)?;
    tracing::info!(root_base = %config.root_base.display(), "exec-server started");
    serve(
        ExecServer::new(config),
        tokio::io::stdin(),
        tokio::io::stdout(),
    )
    .await;
    Ok(())
}
