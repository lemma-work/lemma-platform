//! The host's end of the MCP bridge: agent tool calls, relayed over the link.
//!
//! An agent reaches Lemma's tools through `mcp-bridge`, a stdio MCP server the
//! adapter spawns. The bridge is a separate process, so it cannot use the
//! worker's link directly; it connects here instead, over loopback TCP, and
//! this relay forwards each `tools/list` and `tools/call` as an `mcp` frame.
//!
//! The bridge used to call the workspace's HTTP MCP endpoint itself, with the
//! run's token, and poll a second endpoint every two seconds while an
//! `ask_user` waited on a person. Relaying means one connection per host
//! instead of one request per tool call, a parked call answered the moment
//! the person decides, and a refreshed credential that takes effect on the
//! next call without the bridge re-reading anything: the relay reads the
//! run's current token from the journal each time.
//!
//! Loopback TCP rather than a Unix socket or named pipe, so there is one
//! implementation: a socket path under the data directory can exceed macOS's
//! 104-byte limit, and Windows would need a second transport. The endpoint
//! file is private to this user, and every connection has to present the
//! random token written in it -- the same bar as reading the run's credential
//! out of the journal, which the bridge could always do.

use std::path::PathBuf;
use std::sync::Arc;
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::mpsc;
use uuid::Uuid;

use crate::config::HostPaths;
use crate::journal::Journal;
use crate::link::protocol::{InteractionWaitBody, McpBody};
use crate::link::{LinkError, LinkSlot};

/// How long a call waits for a link before telling the agent Lemma is away.
const LINK_WAIT: Duration = Duration::from_secs(60);
/// How long a parked call waits for a person, matching the half hour a native
/// permission request is held open: it is the same act from their side.
const PARK_TIMEOUT: Duration = Duration::from_secs(30 * 60);
/// The largest line either side accepts.
pub(crate) const MAX_RELAY_LINE: usize = 8 * 1024 * 1024;

/// Where the bridge finds the relay: a port and the token it must present.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub(crate) struct RelayEndpoint {
    pub(crate) port: u16,
    pub(crate) token: String,
}

/// The endpoint file for one target, under the host's private directory.
pub fn endpoint_path(paths: &HostPaths, target_id: Uuid) -> PathBuf {
    paths.root.join("mcp-relay").join(format!("{target_id}.json"))
}

/// One request from the bridge.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub(crate) struct RelayRequest {
    pub(crate) id: u64,
    pub(crate) token: String,
    pub(crate) run_id: Uuid,
    /// `tools/list`, `tools/call` or `interaction_wait`.
    pub(crate) method: String,
    #[serde(default)]
    pub(crate) params: Value,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub(crate) struct RelayResponse {
    pub(crate) id: u64,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) result: Option<Value>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    pub(crate) error: Option<String>,
}

/// Bind the relay and publish its endpoint; the returned future serves it.
pub fn serve(
    paths: &HostPaths,
    target_id: Uuid,
    journal: Journal,
    link: LinkSlot,
) -> anyhow::Result<impl std::future::Future<Output = ()> + Send + 'static> {
    let listener = std::net::TcpListener::bind(("127.0.0.1", 0))?;
    listener.set_nonblocking(true)?;
    let port = listener.local_addr()?.port();
    let mut secret = [0_u8; 32];
    getrandom::fill(&mut secret).map_err(|error| anyhow::anyhow!("no randomness: {error}"))?;
    let token = hex::encode(secret);
    let endpoint = RelayEndpoint {
        port,
        token: token.clone(),
    };
    lemma_private_file::write_atomic(
        &endpoint_path(paths, target_id),
        &serde_json::to_vec(&endpoint)?,
    )?;
    let listener = TcpListener::from_std(listener)?;
    let relay = Arc::new(Relay {
        target_id,
        journal,
        link,
        token,
    });
    Ok(async move {
        loop {
            let (stream, _) = match listener.accept().await {
                Ok(accepted) => accepted,
                Err(error) => {
                    tracing::warn!(%error, "the MCP relay could not accept a connection");
                    tokio::time::sleep(Duration::from_millis(200)).await;
                    continue;
                }
            };
            let relay = Arc::clone(&relay);
            tokio::spawn(async move {
                if let Err(error) = relay.connection(stream).await {
                    tracing::debug!(%error, "an MCP bridge connection ended");
                }
            });
        }
    })
}

struct Relay {
    target_id: Uuid,
    journal: Journal,
    link: LinkSlot,
    token: String,
}

impl Relay {
    async fn connection(self: Arc<Self>, stream: TcpStream) -> anyhow::Result<()> {
        let (reader, mut writer) = stream.into_split();
        let mut lines = BufReader::new(reader).lines();
        let (answers, mut outgoing) = mpsc::unbounded_channel::<RelayResponse>();
        let write = tokio::spawn(async move {
            while let Some(answer) = outgoing.recv().await {
                let Ok(mut line) = serde_json::to_vec(&answer) else {
                    continue;
                };
                line.push(b'\n');
                if writer.write_all(&line).await.is_err() {
                    break;
                }
            }
        });
        while let Some(line) = lines.next_line().await? {
            if line.len() > MAX_RELAY_LINE {
                anyhow::bail!("an MCP bridge request exceeded {MAX_RELAY_LINE} bytes");
            }
            let request: RelayRequest = serde_json::from_str(&line)?;
            // Everything on this connection is refused if the first line is.
            if !constant_time_eq(request.token.as_bytes(), self.token.as_bytes()) {
                anyhow::bail!("an MCP bridge presented the wrong relay token");
            }
            // Many calls can be in flight at once -- an agent that runs tools
            // in parallel -- so each is answered on its own.
            let relay = Arc::clone(&self);
            let answers = answers.clone();
            tokio::spawn(async move {
                let id = request.id;
                let answer = match relay.answer(request).await {
                    Ok(result) => RelayResponse {
                        id,
                        result: Some(result),
                        error: None,
                    },
                    Err(error) => RelayResponse {
                        id,
                        result: None,
                        error: Some(error),
                    },
                };
                let _ = answers.send(answer);
            });
        }
        drop(answers);
        let _ = write.await;
        Ok(())
    }

    /// The run's current Lemma identity, read fresh so a refreshed credential
    /// applies to the very next call.
    fn run_identity(&self, run_id: Uuid) -> Result<(Uuid, String), String> {
        let run = self
            .journal
            .get_run(self.target_id, run_id)
            .map_err(|error| format!("could not read the run: {error}"))?
            .ok_or_else(|| "this run is not one the Agent Host is executing".to_owned())?;
        let mcp = &run.spec.mcp;
        let token = mcp
            .get("token")
            .and_then(Value::as_str)
            .map(str::to_owned)
            .or_else(|| {
                mcp.get("authorization")
                    .and_then(Value::as_str)
                    .map(|value| value.trim_start_matches("Bearer ").to_owned())
            })
            .filter(|token| !token.is_empty())
            .ok_or_else(|| "the run carries no Lemma credential".to_owned())?;
        let conversation_id = mcp
            .get("conversation_id")
            .and_then(Value::as_str)
            .and_then(|id| Uuid::parse_str(id).ok())
            .unwrap_or(run.spec.conversation_id);
        Ok((conversation_id, token))
    }

    async fn answer(&self, request: RelayRequest) -> Result<Value, String> {
        match request.method.as_str() {
            "tools/list" => self.relay_mcp(&request, true).await,
            // Not retried across a dropped link: a tool call has side effects,
            // and one that was in flight when the link went may already have
            // run. The agent is told so and decides.
            "tools/call" => self.relay_mcp(&request, false).await,
            "interaction_wait" => self.wait_for_person(&request).await,
            other => Err(format!("the relay does not know {other}")),
        }
    }

    async fn relay_mcp(&self, request: &RelayRequest, retry: bool) -> Result<Value, String> {
        let mut attempts = 0;
        loop {
            attempts += 1;
            let (conversation_id, token) = self.run_identity(request.run_id)?;
            let link = self.current_link().await?;
            let body = McpBody {
                run_id: request.run_id,
                conversation_id,
                token,
                method: request.method.clone(),
                params: request.params.clone(),
            };
            match link.mcp(&body).await {
                Ok(result) => return Ok(result),
                Err(LinkError::Rejected { message, .. }) => return Err(message),
                Err(error) if retry && attempts < 3 => {
                    tracing::info!(%error, "the link dropped during an MCP request; retrying");
                }
                Err(error) => {
                    return Err(format!(
                        "the connection to Lemma dropped during this tool call, so it may or \
                         may not have run: {error}"
                    ));
                }
            }
        }
    }

    async fn wait_for_person(&self, request: &RelayRequest) -> Result<Value, String> {
        let tool_call_id = request
            .params
            .get("tool_call_id")
            .and_then(Value::as_str)
            .ok_or_else(|| "interaction_wait needs a tool_call_id".to_owned())?
            .to_owned();
        let deadline = tokio::time::Instant::now() + PARK_TIMEOUT;
        loop {
            let (conversation_id, token) = self.run_identity(request.run_id)?;
            let link = self.current_link().await?;
            let body = InteractionWaitBody {
                run_id: request.run_id,
                conversation_id,
                token,
                tool_call_id: tool_call_id.clone(),
            };
            let remaining = deadline.saturating_duration_since(tokio::time::Instant::now());
            if remaining.is_zero() {
                return Err("nobody answered in time".to_owned());
            }
            // Waiting is idempotent, so a dropped link just means asking again
            // on the next one.
            match tokio::time::timeout(remaining, link.interaction_wait(&body)).await {
                Ok(Ok(answer)) => return Ok(answer),
                Ok(Err(LinkError::Rejected { message, .. })) => return Err(message),
                Ok(Err(error)) => {
                    tracing::info!(%error, "the link dropped while waiting for a person; waiting again");
                }
                Err(_) => return Err("nobody answered in time".to_owned()),
            }
        }
    }

    async fn current_link(&self) -> Result<crate::link::LinkHandle, String> {
        let mut slot = self.link.clone();
        match tokio::time::timeout(LINK_WAIT, slot.wait()).await {
            Ok(Some(link)) => Ok(link),
            _ => Err("Lemma cannot be reached from this computer right now".to_owned()),
        }
    }
}

fn constant_time_eq(left: &[u8], right: &[u8]) -> bool {
    left.len() == right.len()
        && left
            .iter()
            .zip(right)
            .fold(0_u8, |difference, (a, b)| difference | (a ^ b))
            == 0
}
