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
use crate::mcp_relay::{MAX_RELAY_LINE, RelayEndpoint, RelayRequest, RelayResponse, endpoint_path};

mod parking;

pub(crate) use parking::*;

#[cfg(test)]
mod tests;

const MAX_MCP_MESSAGE_BYTES: usize = 4 * 1024 * 1024;

/// How long the bridge waits for its host's relay to appear. The adapter
/// starts the bridge while the host is already running the run, so this only
/// covers a relay that is still binding.
const RELAY_WAIT: Duration = Duration::from_secs(30);

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
    let mut lines = BufReader::new(tokio::io::stdin()).lines();
    while let Some(line) = lines.next_line().await? {
        if line.trim().is_empty() {
            continue;
        }
        if line.len() > MAX_MCP_MESSAGE_BYTES {
            anyhow::bail!("MCP input exceeded the {MAX_MCP_MESSAGE_BYTES} byte limit");
        }
        let message: Value = serde_json::from_str(&line).context("MCP input was not valid JSON")?;
        // A notification has no id and wants no answer.
        let Some(id) = message.get("id").filter(|id| !id.is_null()).cloned() else {
            continue;
        };
        let method = message
            .get("method")
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned();
        let params = message.get("params").cloned().unwrap_or(Value::Null);
        let relay = relay.clone();
        let answers = answers.clone();
        // Tool calls run concurrently: an agent that calls tools in parallel
        // should not have them queue behind each other here.
        tokio::spawn(async move {
            let frame = match answer(&relay, &method, params).await {
                Ok(result) => json!({ "jsonrpc": "2.0", "id": id, "result": result }),
                Err(Failure { code, message }) => json!({
                    "jsonrpc": "2.0",
                    "id": id,
                    "error": { "code": code, "message": message },
                }),
            };
            let _ = answers.send(frame);
        });
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
            .request("tools/list", params)
            .await
            .map_err(|message| internal(&message)),
        "tools/call" => {
            let result = relay
                .request("tools/call", params)
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

type Waiters = Arc<Mutex<HashMap<u64, oneshot::Sender<RelayResponse>>>>;

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
        let deadline = tokio::time::Instant::now() + RELAY_WAIT;
        let endpoint: RelayEndpoint = loop {
            match std::fs::read(&self.endpoint_file) {
                Ok(bytes) => break serde_json::from_slice(&bytes)?,
                Err(error) if tokio::time::Instant::now() >= deadline => {
                    return Err(error).context("the Agent Host's MCP relay is not running");
                }
                Err(_) => tokio::time::sleep(Duration::from_millis(200)).await,
            }
        };
        let stream = TcpStream::connect(("127.0.0.1", endpoint.port))
            .await
            .context("could not reach the Agent Host's MCP relay")?;
        let (reader, mut writer) = stream.into_split();
        let (lines, mut outgoing) = mpsc::unbounded_channel::<String>();
        let waiters: Waiters = Arc::new(Mutex::new(HashMap::new()));
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
                    .remove(&response.id);
                if let Some(waiter) = waiter {
                    let _ = waiter.send(response);
                }
            }
            // The relay went away: everyone waiting hears so by their sender
            // being dropped.
            reader_waiters
                .lock()
                .expect("relay waiters poisoned")
                .clear();
        });
        *self.connection.lock().await = Some(RelayConnection {
            token: endpoint.token,
            lines,
            waiters,
        });
        Ok(())
    }

    async fn request(&self, method: &str, params: Value) -> Result<Value, String> {
        // One reconnect: the host may have restarted since the last call.
        for attempt in 0..2 {
            let answered = {
                let mut connection = self.connection.lock().await;
                if connection
                    .as_ref()
                    .is_none_or(|open| open.lines.is_closed())
                {
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
                open.waiters
                    .lock()
                    .expect("relay waiters poisoned")
                    .insert(id, waiter);
                let line = serde_json::to_string(&request).map_err(|error| error.to_string())?;
                if open.lines.send(line).is_err() {
                    *connection = None;
                    continue;
                }
                answered
            };
            match answered.await {
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
