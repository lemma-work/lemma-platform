//! The bridge that gives an agent Lemma's own tools over MCP.
//!
//! The adapter spawns this as a stdio MCP server. It answers the MCP
//! handshake itself and hands every tool listing and call to the Agent Host
//! that owns the run, through the loopback relay in `mcp_relay`, which sends
//! it to Lemma over the link. The bridge holds no credential and talks to no
//! network: which run it serves, and that run's current token, are the
//! host's to know.

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::{Arc, Mutex};
use std::time::Duration;

use anyhow::Context;
use serde_json::{Value, json};
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::TcpStream;
use tokio::sync::{mpsc, oneshot};
use uuid::Uuid;

use crate::config::HostPaths;
use crate::mcp_relay::{
    CANCEL, HELLO, MAX_RELAY_LINE, RelayEndpoint, RelayRequest, RelayResponse, endpoint_path,
};

mod parking;

pub(crate) use parking::*;

#[cfg(test)]
mod tests;

const MAX_MCP_MESSAGE_BYTES: usize = 4 * 1024 * 1024;

/// How long the bridge waits for its host's relay to appear. The adapter
/// starts the bridge while the host is already running the run, so this only
/// covers a relay that is still binding.
const RELAY_WAIT: Duration = Duration::from_secs(30);

/// The longest a listing may take. Past it the agent is told, rather than
/// left waiting on a Lemma that is not going to answer.
const LIST_TIMEOUT: Duration = Duration::from_secs(120);
/// The longest one tool call may take here, parked waits apart. Lemma bounds
/// its own tools well inside this; it is the backstop for an answer that was
/// lost, not a limit a working tool reaches.
const CALL_TIMEOUT: Duration = Duration::from_secs(30 * 60);

/// The MCP revision the bridge answers `initialize` with when the agent does
/// not ask for one.
const DEFAULT_PROTOCOL_VERSION: &str = "2025-06-18";

pub async fn run_bridge(paths: &HostPaths, target_id: Uuid, run_id: Uuid) -> anyhow::Result<()> {
    let relay = RelayClient::connect(paths, target_id, run_id).await?;
    let (answers, mut outgoing) = mpsc::unbounded_channel::<Value>();
    // One writer, so concurrent answers are never interleaved on stdout.
    let writer = tokio::spawn(async move {
        let mut stdout = tokio::io::stdout();
        while let Some(frame) = outgoing.recv().await {
            let Ok(mut line) = serde_json::to_vec(&frame) else {
                continue;
            };
            line.push(b'\n');
            if stdout.write_all(&line).await.is_err() || stdout.flush().await.is_err() {
                break;
            }
        }
    });
    // Requests still being answered, by their JSON-RPC id, so a
    // `notifications/cancelled` can stop one.
    let working: Arc<Mutex<HashMap<String, tokio::task::AbortHandle>>> = Arc::default();
    let mut lines = BufReader::new(tokio::io::stdin()).lines();
    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }
        if line.len() > MAX_MCP_MESSAGE_BYTES {
            anyhow::bail!("MCP input exceeded the {MAX_MCP_MESSAGE_BYTES} byte limit");
        }
        let message: Value = serde_json::from_str(&line).context("MCP input was not valid JSON")?;
        let method = message
            .get("method")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        // A notification has no id and wants no answer. One of them matters:
        // the agent giving up on a request -- a parked question the person
        // will never answer now, say -- which must stop holding it open here
        // and on the relay.
        let Some(id) = message.get("id").filter(|id| !id.is_null()).cloned() else {
            if method == "notifications/cancelled"
                && let Some(cancelled) = message.pointer("/params/requestId")
                && let Some(task) = working
                    .lock()
                    .expect("bridge work poisoned")
                    .remove(&cancelled.to_string())
            {
                task.abort();
            }
            continue;
        };
        let params = message.get("params").cloned().unwrap_or(Value::Null);
        let relay = relay.clone();
        let answers = answers.clone();
        let key = id.to_string();
        let finished = Arc::clone(&working);
        // Tool calls run concurrently: an agent that calls tools in parallel
        // should not have them queue behind each other here.
        let task = tokio::spawn(async move {
            let frame = match answer(&relay, &method, params).await {
                Ok(result) => json!({ "jsonrpc": "2.0", "id": id, "result": result }),
                Err(Failure { code, message }) => json!({
                    "jsonrpc": "2.0",
                    "id": id,
                    "error": { "code": code, "message": message },
                }),
            };
            finished
                .lock()
                .expect("bridge work poisoned")
                .remove(&id.to_string());
            let _ = answers.send(frame);
        });
        working
            .lock()
            .expect("bridge work poisoned")
            .insert(key, task.abort_handle());
    }
    drop(answers);
    let _ = writer.await;
    Ok(())
}

struct Failure {
    code: i64,
    message: String,
}

/// JSON-RPC's code for a request that was well-formed and could not be
/// answered. An agent renders it as a failed tool call, which is the truth
/// and is recoverable -- where a closed pipe would make its whole Lemma
/// toolset vanish.
const INTERNAL_ERROR: i64 = -32_603;
const METHOD_NOT_FOUND: i64 = -32_601;

async fn answer(relay: &RelayClient, method: &str, params: Value) -> Result<Value, Failure> {
    match method {
        "initialize" => Ok(json!({
            "protocolVersion": params
                .get("protocolVersion")
                .and_then(Value::as_str)
                .unwrap_or(DEFAULT_PROTOCOL_VERSION),
            "capabilities": { "tools": { "listChanged": false } },
            "serverInfo": { "name": "lemma", "version": crate::HOST_RELEASE },
        })),
        "ping" => Ok(json!({})),
        "tools/list" => relay
            .request_within("tools/list", params, LIST_TIMEOUT)
            .await
            .map_err(|message| internal(&message)),
        "tools/call" => {
            let result = relay
                .request_within("tools/call", params, CALL_TIMEOUT)
                .await
                .map_err(|message| internal(&message))?;
            // Lemma may have answered "waiting for the person" -- `ask_user`
            // and `request_approval` cannot end the agent's turn from inside a
            // tool call. Hold the call open until they decide, so the model
            // sits inside its turn exactly as it does for one of its own
            // permission requests.
            let Some(tool_call_id) = parked_tool_call_id(&result) else {
                return Ok(result);
            };
            match relay
                .request("interaction_wait", json!({ "tool_call_id": tool_call_id }))
                .await
            {
                Ok(decision) => Ok(result_with_answer(result.clone(), decision).unwrap_or(result)),
                // Unanswered, or Lemma went away. The unchanged result still
                // says it is waiting, which is true and leaves the model able
                // to say so rather than stalling on a promise nobody kept.
                Err(error) => {
                    tracing::warn!(%error, tool_call_id, "a parked tool call was never answered");
                    Ok(result)
                }
            }
        }
        other => Err(Failure {
            code: METHOD_NOT_FOUND,
            message: format!("Lemma's tools do not implement {other}"),
        }),
    }
}

fn internal(message: &str) -> Failure {
    Failure {
        code: INTERNAL_ERROR,
        message: format!("Lemma could not answer this tool call: {message}"),
    }
}

/// The requests waiting on one relay connection, and whether it is gone.
///
/// One lock for both, so a request either registers before the connection is
/// marked gone -- and hears so when its sender is dropped -- or sees the mark
/// and reconnects. Registering on a connection whose reader had already
/// exited was a wait nobody would ever end.
#[derive(Default)]
struct Waiting {
    gone: bool,
    waiters: HashMap<u64, oneshot::Sender<RelayResponse>>,
}

type Waiters = Arc<Mutex<Waiting>>;

/// The bridge's connection to its host's relay, reopened if the host restarts.
#[derive(Clone)]
struct RelayClient {
    endpoint_file: std::path::PathBuf,
    run_id: Uuid,
    next_id: Arc<AtomicU64>,
    connection: Arc<tokio::sync::Mutex<Option<RelayConnection>>>,
}

struct RelayConnection {
    token: String,
    lines: mpsc::UnboundedSender<String>,
    waiters: Waiters,
}

impl RelayClient {
    async fn connect(paths: &HostPaths, target_id: Uuid, run_id: Uuid) -> anyhow::Result<Self> {
        let client = Self {
            endpoint_file: endpoint_path(paths, target_id),
            run_id,
            next_id: Arc::new(AtomicU64::new(1)),
            connection: Arc::new(tokio::sync::Mutex::new(None)),
        };
        // Connected up front so a host that is not there fails the bridge now,
        // where the adapter reports it, rather than on the first tool call.
        client.open().await?;
        Ok(client)
    }

    async fn open(&self) -> anyhow::Result<()> {
        // The endpoint file outlives the relay that wrote it: after the host
        // restarts it names a port nothing listens on until the new relay
        // rewrites it. So a refused connection is waited out like a missing
        // file, re-reading the file each time.
        let deadline = tokio::time::Instant::now() + RELAY_WAIT;
        let (endpoint, stream) = loop {
            let attempt = async {
                let bytes = std::fs::read(&self.endpoint_file)
                    .context("the Agent Host's MCP relay is not running")?;
                let endpoint: RelayEndpoint = serde_json::from_slice(&bytes)?;
                let stream = TcpStream::connect(("127.0.0.1", endpoint.port))
                    .await
                    .context("could not reach the Agent Host's MCP relay")?;
                anyhow::Ok((endpoint, stream))
            };
            match attempt.await {
                Ok(opened) => break opened,
                Err(error) if tokio::time::Instant::now() >= deadline => return Err(error),
                Err(_) => tokio::time::sleep(Duration::from_millis(200)).await,
            }
        };
        let (reader, mut writer) = stream.into_split();
        let (lines, mut outgoing) = mpsc::unbounded_channel::<String>();
        let waiters: Waiters = Arc::default();
        // The token first, on a line of its own: the relay gives a connection
        // a few seconds and a small first line to prove it holds it.
        let hello = serde_json::to_string(&RelayRequest {
            id: 0,
            token: endpoint.token.clone(),
            run_id: self.run_id,
            method: HELLO.to_owned(),
            params: Value::Null,
        })?;
        let _ = lines.send(hello);
        tokio::spawn(async move {
            while let Some(mut line) = outgoing.recv().await {
                line.push('\n');
                if writer.write_all(line.as_bytes()).await.is_err() {
                    break;
                }
            }
        });
        let reader_waiters = Arc::clone(&waiters);
        tokio::spawn(async move {
            let mut lines = BufReader::new(reader).lines();
            while let Ok(Some(line)) = lines.next_line().await {
                if line.len() > MAX_RELAY_LINE {
                    break;
                }
                let Ok(response) = serde_json::from_str::<RelayResponse>(&line) else {
                    continue;
                };
                let waiter = reader_waiters
                    .lock()
                    .expect("relay waiters poisoned")
                    .waiters
                    .remove(&response.id);
                if let Some(waiter) = waiter {
                    let _ = waiter.send(response);
                }
            }
            // The relay went away: everyone waiting hears so by their sender
            // being dropped, and nobody registers here again.
            let mut waiting = reader_waiters.lock().expect("relay waiters poisoned");
            waiting.gone = true;
            waiting.waiters.clear();
        });
        *self.connection.lock().await = Some(RelayConnection {
            token: endpoint.token,
            lines,
            waiters,
        });
        Ok(())
    }

    /// `request`, abandoned -- and the relay told to stop -- after `limit`.
    async fn request_within(
        &self,
        method: &str,
        params: Value,
        limit: Duration,
    ) -> Result<Value, String> {
        tokio::time::timeout(limit, self.request(method, params))
            .await
            .unwrap_or_else(|_| {
                Err(format!(
                    "Lemma did not answer within {} minutes",
                    limit.as_secs() / 60
                ))
            })
    }

    async fn request(&self, method: &str, params: Value) -> Result<Value, String> {
        // One reconnect: the host may have restarted since the last call.
        for attempt in 0..2 {
            let (answered, mut stop) = {
                let mut connection = self.connection.lock().await;
                if connection.as_ref().is_none_or(RelayConnection::is_gone) {
                    drop(connection);
                    self.open().await.map_err(|error| error.to_string())?;
                    connection = self.connection.lock().await;
                }
                let Some(open) = connection.as_ref() else {
                    return Err("the Agent Host's MCP relay is not running".to_owned());
                };
                let id = self.next_id.fetch_add(1, Ordering::Relaxed);
                let request = RelayRequest {
                    id,
                    token: open.token.clone(),
                    run_id: self.run_id,
                    method: method.to_owned(),
                    params: params.clone(),
                };
                let (waiter, answered) = oneshot::channel();
                {
                    let mut waiting = open.waiters.lock().expect("relay waiters poisoned");
                    if waiting.gone {
                        drop(waiting);
                        *connection = None;
                        continue;
                    }
                    waiting.waiters.insert(id, waiter);
                }
                let line = serde_json::to_string(&request).map_err(|error| error.to_string())?;
                if open.lines.send(line).is_err() {
                    *connection = None;
                    continue;
                }
                // Dropped with this future -- the agent cancelled, or a
                // deadline passed -- it tells the relay to stop too.
                let stop = StopOnDrop {
                    lines: open.lines.clone(),
                    token: open.token.clone(),
                    run_id: self.run_id,
                    id,
                    armed: true,
                };
                (answered, stop)
            };
            let outcome = answered.await;
            stop.armed = false;
            match outcome {
                Ok(RelayResponse {
                    result: Some(result),
                    ..
                }) => return Ok(result),
                Ok(RelayResponse { error, .. }) => {
                    return Err(error.unwrap_or_else(|| "the relay answered nothing".to_owned()));
                }
                // The relay dropped mid-request. A listing is safe to ask
                // again; a tool call may already have run, so it is reported.
                Err(_) if attempt == 0 && method != "tools/call" => {
                    *self.connection.lock().await = None;
                }
                Err(_) => {
                    return Err(
                        "the Agent Host restarted during this tool call, so it may or may not \
                         have run"
                            .to_owned(),
                    );
                }
            }
        }
        Err("the Agent Host's MCP relay is not reachable".to_owned())
    }
}

impl RelayConnection {
    fn is_gone(&self) -> bool {
        self.lines.is_closed() || self.waiters.lock().expect("relay waiters poisoned").gone
    }
}

/// Tells the relay to stop working on a request nobody is waiting for any
/// more, unless disarmed because the answer came.
struct StopOnDrop {
    lines: mpsc::UnboundedSender<String>,
    token: String,
    run_id: Uuid,
    id: u64,
    armed: bool,
}

impl Drop for StopOnDrop {
    fn drop(&mut self) {
        if !self.armed {
            return;
        }
        if let Ok(line) = serde_json::to_string(&RelayRequest {
            id: 0,
            token: self.token.clone(),
            run_id: self.run_id,
            method: CANCEL.to_owned(),
            params: json!({ "id": self.id }),
        }) {
            let _ = self.lines.send(line);
        }
    }
}
