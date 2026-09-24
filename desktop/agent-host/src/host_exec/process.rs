//! The processes one exec-server runs: started, read, fed, resized, stopped.
//!
//! Every process leads its own process group (a PTY's child leads its own
//! session, which is also a group), so "stop this command" reaches everything
//! it started -- the dev server `npm run dev` forked included.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, PoisonError};
use std::time::{Duration, Instant};

use base64::Engine;
use base64::engine::general_purpose::STANDARD;
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::sync::watch;

use super::paths::{Leaf, PathPolicy};
use super::ring::{OutputRing, Stream};
use super::wire::{OP_MAX_DATA_BYTES, OpFailure, kind};

/// How long an exited process is kept for someone to read.
pub const RETAIN_AFTER_EXIT: Duration = Duration::from_secs(10 * 60);
/// How long a process that has also been read to the end is kept. Not zero:
/// the read that drained it may be retried if its answer was lost.
pub const RETAIN_AFTER_DRAINED: Duration = Duration::from_secs(60);
/// The ring size when a request names none.
pub const DEFAULT_OUTPUT_LIMIT: usize = 1024 * 1024;
/// The largest ring a request may ask for.
pub const MAX_OUTPUT_LIMIT: usize = 16 * 1024 * 1024;
/// The longest one `process.read` waits for output.
pub const MAX_READ_WAIT: Duration = Duration::from_secs(30);
/// How long output may keep arriving after the process itself exited. A
/// background child that inherited the pipe can hold it open for ever; what
/// it writes after this still lands in the ring, but the process is reported
/// exited without waiting for it.
const DRAIN_AFTER_EXIT: Duration = Duration::from_secs(2);
/// How much one read of a stream takes at a time.
const READ_CHUNK: usize = 64 * 1024;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum State {
    Running,
    Exited,
    Killed,
}

impl State {
    const fn name(self) -> &'static str {
        match self {
            Self::Running => "running",
            Self::Exited => "exited",
            Self::Killed => "killed",
        }
    }
}

#[derive(Debug)]
struct Status {
    state: State,
    exit_code: Option<i32>,
    exited_at: Option<Instant>,
    drained_at: Option<Instant>,
}

enum Input {
    Pipe(tokio::process::ChildStdin),
    Pty(Arc<Mutex<Box<dyn Write + Send>>>),
    Closed,
}

struct Managed {
    id: String,
    command: String,
    started_at: chrono::DateTime<chrono::Utc>,
    /// The process group, which is the leader's pid.
    group: Option<i32>,
    status: Mutex<Status>,
    ring: Mutex<OutputRing>,
    /// Bumped on new output and on exit; a waiting read watches it.
    changed: watch::Sender<u64>,
    input: tokio::sync::Mutex<Input>,
    pty: Option<Mutex<Box<dyn portable_pty::MasterPty + Send>>>,
    terminated: AtomicBool,
}

impl Managed {
    fn push(&self, stream: Stream, data: Vec<u8>) {
        lock(&self.ring).push(stream, data);
        self.changed.send_modify(|count| *count += 1);
    }

    fn finish(&self, exit_code: Option<i32>, signalled: bool) {
        {
            let mut status = lock(&self.status);
            status.state = if signalled || self.terminated.load(Ordering::SeqCst) {
                State::Killed
            } else {
                State::Exited
            };
            status.exit_code = exit_code;
            status.exited_at = Some(Instant::now());
        }
        self.changed.send_modify(|count| *count += 1);
    }

    fn state(&self) -> State {
        lock(&self.status).state
    }

    fn signal_group(&self, signal: rustix::process::Signal) {
        let Some(pid) = self.group.and_then(rustix::process::Pid::from_raw) else {
            return;
        };
        // ESRCH once everything in the group is gone, which is the point.
        let _ = rustix::process::kill_process_group(pid, signal);
    }

    fn group_alive(&self) -> bool {
        self.group
            .and_then(rustix::process::Pid::from_raw)
            .is_some_and(|pid| rustix::process::test_kill_process_group(pid).is_ok())
    }
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    mutex.lock().unwrap_or_else(PoisonError::into_inner)
}

#[derive(Default)]
struct Tables {
    by_id: HashMap<String, Arc<Managed>>,
    /// `operation_id` to `process_id`, so a `process.start` retried after a
    /// lost answer finds the process it already started instead of a second.
    by_operation: HashMap<String, String>,
}

/// One workspace's processes.
#[derive(Clone, Default)]
pub struct ProcessTable {
    tables: Arc<Mutex<Tables>>,
}

#[derive(Deserialize)]
struct EnvironmentEntry {
    name: String,
    value: String,
}

#[derive(Deserialize)]
struct Tty {
    rows: u16,
    cols: u16,
}

#[derive(Deserialize)]
struct StartParams {
    #[serde(default)]
    operation_id: Option<String>,
    #[serde(default)]
    shell_command: Option<String>,
    #[serde(default)]
    argv: Option<Vec<String>>,
    #[serde(default)]
    cwd: Option<String>,
    #[serde(default)]
    environment: Vec<EnvironmentEntry>,
    #[serde(default)]
    tty: Option<Tty>,
    #[serde(default)]
    output_limit_bytes: Option<usize>,
    #[serde(default)]
    initial_input: Option<String>,
}

fn params<T: serde::de::DeserializeOwned>(value: Value) -> Result<T, OpFailure> {
    serde_json::from_value(value).map_err(|error| OpFailure::invalid(error.to_string()))
}

pub(crate) fn decode(data: &str) -> Result<Vec<u8>, OpFailure> {
    let bytes = STANDARD
        .decode(data)
        .map_err(|error| OpFailure::invalid(format!("data is not base64: {error}")))?;
    if bytes.len() > OP_MAX_DATA_BYTES {
        return Err(OpFailure::new(
            kind::TOO_LARGE,
            format!("at most {OP_MAX_DATA_BYTES} bytes travel in one op"),
        ));
    }
    Ok(bytes)
}

/// The shell a `shell_command` runs under. Bash, as in the VM sandbox, so the
/// same command means the same thing in both; without `-l`, because the
/// environment is already the owner's login shell's.
fn shell() -> &'static str {
    if std::path::Path::new("/bin/bash").exists() {
        "/bin/bash"
    } else {
        "/bin/sh"
    }
}

impl ProcessTable {
    fn get(&self, id: &str) -> Result<Arc<Managed>, OpFailure> {
        lock(&self.tables)
            .by_id
            .get(id)
            .cloned()
            .ok_or_else(|| OpFailure::new(kind::PROCESS_NOT_FOUND, format!("no process {id}")))
    }

    pub async fn start(&self, policy: &PathPolicy, raw: Value) -> Result<Value, OpFailure> {
        let request: StartParams = params(raw)?;
        if let Some(operation) = &request.operation_id
            && let Some(existing) = lock(&self.tables).by_operation.get(operation)
        {
            return Ok(json!({ "process_id": existing }));
        }
        let (program, arguments, command) = match (&request.shell_command, &request.argv) {
            (Some(command), None) => (
                shell().to_owned(),
                vec!["-c".to_owned(), command.clone()],
                command.clone(),
            ),
            (None, Some(argv)) if !argv.is_empty() => {
                (argv[0].clone(), argv[1..].to_vec(), argv.join(" "))
            }
            _ => {
                return Err(OpFailure::invalid(
                    "exactly one of shell_command and a non-empty argv is required",
                ));
            }
        };
        let cwd: PathBuf = match request.cwd.as_deref() {
            Some(cwd) => policy.resolve(cwd, Leaf::Follow)?,
            None => policy.root().to_path_buf(),
        };
        if !cwd.is_dir() {
            return Err(OpFailure::new(
                kind::NOT_A_DIRECTORY,
                format!("{} is not a folder", cwd.display()),
            ));
        }
        let limit = request
            .output_limit_bytes
            .unwrap_or(DEFAULT_OUTPUT_LIMIT)
            .clamp(1, MAX_OUTPUT_LIMIT);
        let initial_input = request.initial_input.as_deref().map(decode).transpose()?;
        // The exec-server's own environment is the scrubbed login snapshot the
        // relay started it with; what the request names goes on top.
        let mut environment: std::collections::BTreeMap<String, String> =
            super::env::scrub(std::env::vars().collect());
        for entry in request.environment {
            environment.insert(entry.name, entry.value);
        }
        let id = uuid::Uuid::new_v4().to_string();
        let spawn = Spawn {
            id: id.clone(),
            program,
            arguments,
            command,
            cwd,
            environment,
            limit,
        };
        let managed = match request.tty {
            Some(tty) => spawn.pty(&tty)?,
            None => spawn.pipes()?,
        };
        if let Some(data) = initial_input {
            write_input(&managed, data).await?;
        }
        let mut tables = lock(&self.tables);
        if let Some(operation) = request.operation_id {
            tables.by_operation.insert(operation, id.clone());
        }
        tables.by_id.insert(id.clone(), managed);
        Ok(json!({ "process_id": id }))
    }

    pub async fn read(&self, raw: Value) -> Result<Value, OpFailure> {
        #[derive(Deserialize)]
        struct ReadParams {
            process_id: String,
            #[serde(default)]
            after_sequence: u64,
            #[serde(default)]
            wait_ms: u64,
        }
        let request: ReadParams = params(raw)?;
        let managed = self.get(&request.process_id)?;
        let deadline =
            tokio::time::Instant::now() + Duration::from_millis(request.wait_ms).min(MAX_READ_WAIT);
        let mut changed = managed.changed.subscribe();
        loop {
            changed.borrow_and_update();
            let has_new = lock(&managed.ring).next_sequence() > request.after_sequence + 1;
            if has_new || managed.state() != State::Running {
                break;
            }
            if tokio::time::timeout_at(deadline, changed.changed())
                .await
                .is_err()
            {
                break;
            }
        }
        let snapshot = lock(&managed.ring).read(request.after_sequence);
        let mut status = lock(&managed.status);
        let read_to_end = snapshot
            .chunks
            .last()
            .map_or(request.after_sequence, |chunk| chunk.sequence)
            + 1
            >= snapshot.next_sequence;
        if status.state != State::Running && read_to_end && status.drained_at.is_none() {
            status.drained_at = Some(Instant::now());
        }
        Ok(json!({
            "chunks": snapshot.chunks.iter().map(|chunk| json!({
                "sequence": chunk.sequence,
                "stream": chunk.stream,
                "data": STANDARD.encode(&chunk.data),
            })).collect::<Vec<_>>(),
            "next_sequence": snapshot.next_sequence,
            "truncated_before_sequence": snapshot.truncated_before_sequence,
            "state": status.state.name(),
            "exit_code": status.exit_code,
        }))
    }

    pub async fn input(&self, raw: Value) -> Result<Value, OpFailure> {
        #[derive(Deserialize)]
        struct InputParams {
            process_id: String,
            data: String,
        }
        let request: InputParams = params(raw)?;
        let managed = self.get(&request.process_id)?;
        write_input(&managed, decode(&request.data)?).await?;
        Ok(json!({}))
    }

    pub fn resize(&self, raw: Value) -> Result<Value, OpFailure> {
        #[derive(Deserialize)]
        struct ResizeParams {
            process_id: String,
            rows: u16,
            cols: u16,
        }
        let request: ResizeParams = params(raw)?;
        let managed = self.get(&request.process_id)?;
        let Some(pty) = &managed.pty else {
            return Err(OpFailure::invalid(
                "only a process started with a tty can be resized",
            ));
        };
        lock(pty)
            .resize(portable_pty::PtySize {
                rows: request.rows,
                cols: request.cols,
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(|error| OpFailure::new(kind::IO_ERROR, error.to_string()))?;
        Ok(json!({}))
    }

    /// SIGTERM to the whole group, then SIGKILL to whatever of it is left
    /// once `grace_ms` is up -- including children that outlived the leader.
    pub async fn terminate(&self, raw: Value) -> Result<Value, OpFailure> {
        #[derive(Deserialize)]
        struct TerminateParams {
            process_id: String,
            #[serde(default = "default_grace")]
            grace_ms: u64,
        }
        fn default_grace() -> u64 {
            2_000
        }
        let request: TerminateParams = params(raw)?;
        let managed = self.get(&request.process_id)?;
        terminate(
            &managed,
            Duration::from_millis(request.grace_ms.min(60_000)),
        )
        .await;
        Ok(json!({}))
    }

    pub fn list(&self) -> Value {
        let tables = lock(&self.tables);
        let mut processes: Vec<_> = tables.by_id.values().collect();
        processes.sort_by_key(|managed| managed.started_at);
        json!({
            "processes": processes.iter().map(|managed| {
                let status = lock(&managed.status);
                json!({
                    "process_id": managed.id,
                    "command": managed.command,
                    "state": status.state.name(),
                    "exit_code": status.exit_code,
                    "started_at": managed.started_at.to_rfc3339(),
                })
            }).collect::<Vec<_>>(),
        })
    }

    /// Forget processes nobody will read again.
    pub fn reap(&self) {
        let now = Instant::now();
        let mut tables = lock(&self.tables);
        let expired: Vec<String> = tables
            .by_id
            .values()
            .filter(|managed| {
                let status = lock(&managed.status);
                status.state != State::Running
                    && (status
                        .exited_at
                        .is_some_and(|at| now.duration_since(at) >= RETAIN_AFTER_EXIT)
                        || status
                            .drained_at
                            .is_some_and(|at| now.duration_since(at) >= RETAIN_AFTER_DRAINED))
            })
            .map(|managed| managed.id.clone())
            .collect();
        for id in &expired {
            tables.by_id.remove(id);
        }
        tables
            .by_operation
            .retain(|_, process| !expired.contains(process));
    }

    /// Kill every process group this table started, at once. For a workspace
    /// closing and an exec-server going away: nothing is left running behind
    /// a workspace nobody can reach any more.
    pub fn kill_all(&self) {
        let processes: Vec<_> = lock(&self.tables).by_id.values().cloned().collect();
        for managed in processes {
            managed.terminated.store(true, Ordering::SeqCst);
            managed.signal_group(rustix::process::Signal::KILL);
        }
    }
}

async fn terminate(managed: &Managed, grace: Duration) {
    managed.terminated.store(true, Ordering::SeqCst);
    if managed.state() == State::Running || managed.group_alive() {
        managed.signal_group(rustix::process::Signal::TERM);
        let mut changed = managed.changed.subscribe();
        let deadline = tokio::time::Instant::now() + grace;
        while managed.state() == State::Running {
            if tokio::time::timeout_at(deadline, changed.changed())
                .await
                .is_err()
            {
                break;
            }
        }
    }
    if managed.group_alive() {
        managed.signal_group(rustix::process::Signal::KILL);
    }
}

async fn write_input(managed: &Managed, data: Vec<u8>) -> Result<(), OpFailure> {
    let mut input = managed.input.lock().await;
    let closed = || OpFailure::new(kind::IO_ERROR, "the process no longer takes input");
    match &mut *input {
        Input::Pipe(stdin) => {
            if let Err(error) = stdin.write_all(&data).await {
                *input = Input::Closed;
                return Err(OpFailure::new(kind::IO_ERROR, error.to_string()));
            }
            Ok(())
        }
        Input::Pty(writer) => {
            let writer = Arc::clone(writer);
            tokio::task::spawn_blocking(move || {
                let mut writer = lock(&writer);
                writer.write_all(&data)?;
                writer.flush()
            })
            .await
            .map_err(|error| OpFailure::new(kind::IO_ERROR, error.to_string()))?
            .map_err(|error| OpFailure::new(kind::IO_ERROR, error.to_string()))
        }
        Input::Closed => Err(closed()),
    }
}

struct Spawn {
    id: String,
    program: String,
    arguments: Vec<String>,
    command: String,
    cwd: PathBuf,
    environment: std::collections::BTreeMap<String, String>,
    limit: usize,
}

impl Spawn {
    fn managed(
        &self,
        group: Option<i32>,
        input: Input,
        pty: Option<Box<dyn portable_pty::MasterPty + Send>>,
    ) -> Arc<Managed> {
        Arc::new(Managed {
            id: self.id.clone(),
            command: self.command.clone(),
            started_at: chrono::Utc::now(),
            group,
            status: Mutex::new(Status {
                state: State::Running,
                exit_code: None,
                exited_at: None,
                drained_at: None,
            }),
            ring: Mutex::new(OutputRing::new(self.limit)),
            changed: watch::channel(0).0,
            input: tokio::sync::Mutex::new(input),
            pty: pty.map(Mutex::new),
            terminated: AtomicBool::new(false),
        })
    }

    fn spawn_failure(&self, error: &std::io::Error) -> OpFailure {
        OpFailure::io(error, std::path::Path::new(&self.program))
    }

    fn pipes(self) -> Result<Arc<Managed>, OpFailure> {
        let mut command = tokio::process::Command::new(&self.program);
        command
            .args(&self.arguments)
            .env_clear()
            .envs(&self.environment)
            .current_dir(&self.cwd)
            .stdin(std::process::Stdio::piped())
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::piped())
            .process_group(0);
        let mut child = command
            .spawn()
            .map_err(|error| self.spawn_failure(&error))?;
        let group = child.id().and_then(|pid| i32::try_from(pid).ok());
        let stdin = child.stdin.take().map_or(Input::Closed, Input::Pipe);
        let stdout = child.stdout.take();
        let stderr = child.stderr.take();
        let managed = self.managed(group, stdin, None);
        let mut pumps = Vec::new();
        if let Some(stream) = stdout {
            pumps.push(tokio::spawn(pump(
                Arc::clone(&managed),
                Stream::Stdout,
                stream,
            )));
        }
        if let Some(stream) = stderr {
            pumps.push(tokio::spawn(pump(
                Arc::clone(&managed),
                Stream::Stderr,
                stream,
            )));
        }
        let waiter = Arc::clone(&managed);
        tokio::spawn(async move {
            let status = child.wait().await;
            let drained = futures_util::future::join_all(pumps);
            let _ = tokio::time::timeout(DRAIN_AFTER_EXIT, drained).await;
            match status {
                Ok(status) => {
                    use std::os::unix::process::ExitStatusExt;
                    waiter.finish(status.code(), status.signal().is_some());
                }
                Err(error) => {
                    tracing::warn!(%error, "could not wait for a host process");
                    waiter.finish(None, false);
                }
            }
        });
        Ok(managed)
    }

    fn pty(self, tty: &Tty) -> Result<Arc<Managed>, OpFailure> {
        let io_error = |error: anyhow::Error| OpFailure::new(kind::IO_ERROR, error.to_string());
        let system = portable_pty::native_pty_system();
        let pair = system
            .openpty(portable_pty::PtySize {
                rows: tty.rows.max(1),
                cols: tty.cols.max(1),
                pixel_width: 0,
                pixel_height: 0,
            })
            .map_err(io_error)?;
        let mut builder = portable_pty::CommandBuilder::new(&self.program);
        builder.args(&self.arguments);
        builder.env_clear();
        for (name, value) in &self.environment {
            builder.env(name, value);
        }
        builder.cwd(&self.cwd);
        let mut child = pair
            .slave
            .spawn_command(builder)
            .map_err(|error| match error.downcast::<std::io::Error>() {
                Ok(error) => self.spawn_failure(&error),
                Err(error) => io_error(error),
            })?;
        drop(pair.slave);
        let group = child.process_id().and_then(|pid| i32::try_from(pid).ok());
        let mut reader = pair.master.try_clone_reader().map_err(io_error)?;
        let writer = pair.master.take_writer().map_err(io_error)?;
        let managed = self.managed(
            group,
            Input::Pty(Arc::new(Mutex::new(writer))),
            Some(pair.master),
        );
        // The PTY's ends are blocking file descriptors, so they get threads
        // of their own rather than a runtime worker each.
        let output = Arc::clone(&managed);
        let pumped = std::thread::spawn(move || {
            let mut buffer = vec![0_u8; READ_CHUNK];
            loop {
                match reader.read(&mut buffer) {
                    // EIO is how a PTY says its last writer has gone.
                    Ok(0) | Err(_) => break,
                    Ok(read) => output.push(Stream::Pty, buffer[..read].to_vec()),
                }
            }
        });
        let waiter = Arc::clone(&managed);
        std::thread::spawn(move || {
            let status = child.wait();
            let deadline = Instant::now() + DRAIN_AFTER_EXIT;
            while !pumped.is_finished() && Instant::now() < deadline {
                std::thread::sleep(Duration::from_millis(10));
            }
            match status {
                Ok(status) => {
                    let signalled = status.signal().is_some();
                    let code =
                        (!signalled).then(|| i32::try_from(status.exit_code()).unwrap_or(-1));
                    waiter.finish(code, signalled);
                }
                Err(error) => {
                    tracing::warn!(%error, "could not wait for a host process");
                    waiter.finish(None, false);
                }
            }
        });
        Ok(managed)
    }
}

async fn pump<R: tokio::io::AsyncRead + Unpin>(
    managed: Arc<Managed>,
    stream: Stream,
    mut reader: R,
) {
    let mut buffer = vec![0_u8; READ_CHUNK];
    loop {
        match reader.read(&mut buffer).await {
            Ok(0) | Err(_) => return,
            Ok(read) => managed.push(stream, buffer[..read].to_vec()),
        }
    }
}
