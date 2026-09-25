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

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::{Arc, Mutex, PoisonError};
use std::time::Duration;

use serde::{Deserialize, Serialize};
use serde_json::Value;
use tokio::io::{AsyncBufRead, AsyncBufReadExt, AsyncReadExt, AsyncWriteExt, BufReader};
use tokio::net::{TcpListener, TcpStream};
use tokio::sync::{Semaphore, mpsc};
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
/// How many times one MCP request is tried before its failure goes to the
/// agent, which can say so or work around it -- better than a bridge that
/// stalls behind a Lemma that is genuinely down.
const MAX_ATTEMPTS: u32 = 3;
/// The pause before trying again, growing with each attempt.
const RETRY_PAUSE: Duration = Duration::from_millis(500);
/// The largest line either side accepts.
pub(crate) const MAX_RELAY_LINE: usize = 8 * 1024 * 1024;
/// The largest first line, which is read before the connection has shown it
/// holds the token. The loopback port is open to every account on this Mac;
/// what a stranger can make this process hold is bounded by this, the
/// deadline below and the connection cap.
const MAX_FIRST_LINE: usize = 64 * 1024;
/// How long a new connection has to present the token.
const AUTH_DEADLINE: Duration = Duration::from_secs(5);
/// Bridge connections held at once. One per running agent is the workload.
const MAX_CONNECTIONS: usize = 64;
/// How often a parked wait checks whether its run has ended.
const RUN_CHECK: Duration = Duration::from_secs(5);

/// The first line a bridge sends: the token, and nothing to answer.
pub(crate) const HELLO: &str = "hello";
/// Stop working on an earlier request of this connection, named by `id` in
/// `params`: the agent cancelled it.
pub(crate) const CANCEL: &str = "cancel";

/// Where the bridge finds the relay: a port and the token it must present.
#[derive(Clone, Debug, Serialize, Deserialize)]
pub(crate) struct RelayEndpoint {
    pub(crate) port: u16,
    pub(crate) token: String,
}

/// The endpoint file for one target, under the host's private directory.
#[must_use]
pub fn endpoint_path(paths: &HostPaths, target_id: Uuid) -> PathBuf {
    paths
        .root
        .join("mcp-relay")
        .join(format!("{target_id}.json"))
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
    let connections = Arc::new(Semaphore::new(MAX_CONNECTIONS));
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
            let Ok(slot) = Arc::clone(&connections).try_acquire_owned() else {
                tracing::warn!(
                    "the MCP relay is holding as many connections as it will; refused one"
                );
                continue;
            };
            let relay = Arc::clone(&relay);
            tokio::spawn(async move {
                let _slot = slot;
                if let Err(error) = relay.connection(stream).await {
                    tracing::debug!(%error, "an MCP bridge connection ended");
                }
            });
        }
    })
}

/// One line of at most `limit` bytes, without its newline; `None` at the end
/// of the stream. A longer line is an error, found without holding more than
/// `limit` bytes of it.
async fn read_line_bounded<R: AsyncBufRead + Unpin>(
    reader: &mut R,
    limit: usize,
) -> anyhow::Result<Option<String>> {
    let mut line = Vec::new();
    let read = (&mut *reader)
        .take(u64::try_from(limit).unwrap_or(u64::MAX) + 1)
        .read_until(b'\n', &mut line)
        .await?;
    if read == 0 {
        return Ok(None);
    }
    if line.last() == Some(&b'\n') {
        line.pop();
    } else if line.len() > limit {
        anyhow::bail!("an MCP bridge line exceeded {limit} bytes");
    }
    Ok(Some(String::from_utf8(line)?))
}

/// An answer as it goes on the wire: one that would not fit in a line the
/// bridge accepts is replaced by an error saying so, rather than a line the
/// bridge would hang up on.
fn wire_line(answer: &RelayResponse) -> Option<Vec<u8>> {
    let mut line = serde_json::to_vec(answer).ok()?;
    if line.len() > MAX_RELAY_LINE {
        line = serde_json::to_vec(&RelayResponse {
            id: answer.id,
            result: None,
            error: Some(format!(
                "Lemma's answer was {} bytes, more than the {MAX_RELAY_LINE} one tool result may be",
                line.len()
            )),
        })
        .ok()?;
    }
    line.push(b'\n');
    Some(line)
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
        let mut reader = BufReader::new(reader);
        // Everything on this connection is refused if the first line is: it
        // has a few seconds and a small budget to show it holds the token.
        let first = tokio::time::timeout(
            AUTH_DEADLINE,
            read_line_bounded(&mut reader, MAX_FIRST_LINE),
        )
        .await
        .map_err(|_| anyhow::anyhow!("an MCP bridge did not present the relay token in time"))??
        .ok_or_else(|| anyhow::anyhow!("an MCP bridge hung up before presenting the token"))?;
        let first: RelayRequest = serde_json::from_str(&first)?;
        if !constant_time_eq(first.token.as_bytes(), self.token.as_bytes()) {
            anyhow::bail!("an MCP bridge presented the wrong relay token");
        }
        let (answers, mut outgoing) = mpsc::unbounded_channel::<RelayResponse>();
        let write = tokio::spawn(async move {
            while let Some(answer) = outgoing.recv().await {
                let Some(line) = wire_line(&answer) else {
                    continue;
                };
                if writer.write_all(&line).await.is_err() {
                    break;
                }
            }
        });
        let working: Arc<Mutex<HashMap<u64, tokio::task::AbortHandle>>> = Arc::default();
        let mut next = (first.method != HELLO).then_some(first);
        loop {
            let request = match next.take() {
                Some(request) => request,
                None => match read_line_bounded(&mut reader, MAX_RELAY_LINE).await? {
                    Some(line) => serde_json::from_str::<RelayRequest>(&line)?,
                    None => break,
                },
            };
            if !constant_time_eq(request.token.as_bytes(), self.token.as_bytes()) {
                anyhow::bail!("an MCP bridge presented the wrong relay token");
            }
            match request.method.as_str() {
                HELLO => continue,
                CANCEL => {
                    let cancelled = request.params.get("id").and_then(Value::as_u64);
                    if let Some(task) = cancelled.and_then(|id| {
                        working
                            .lock()
                            .unwrap_or_else(PoisonError::into_inner)
                            .remove(&id)
                    }) {
                        task.abort();
                    }
                    continue;
                }
                _ => {}
            }
            // Many calls can be in flight at once -- an agent that runs tools
            // in parallel -- so each is answered on its own.
            let relay = Arc::clone(&self);
            let answers = answers.clone();
            let id = request.id;
            let finished = Arc::clone(&working);
            let task = tokio::spawn(async move {
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
                finished
                    .lock()
                    .unwrap_or_else(PoisonError::into_inner)
                    .remove(&id);
                let _ = answers.send(answer);
            });
            working
                .lock()
                .unwrap_or_else(PoisonError::into_inner)
                .insert(id, task.abort_handle());
        }
        // The bridge is gone, and with it whoever would read these answers.
        for (_, task) in working
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
            .drain()
        {
            task.abort();
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
        // A run that has ended has no tools: its credential may still be
        // valid for a while, but nothing this host runs acts for it any more.
        if run.state.is_terminal() {
            return Err("this run has ended".to_owned());
        }
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
            "tools/list" => self.relay_mcp(&request, None).await,
            // One id for every try of this call: Lemma runs it once per id and
            // answers a repeat with the result it kept.
            "tools/call" => {
                let request_id = Uuid::new_v4().simple().to_string();
                self.relay_mcp(&request, Some(request_id)).await
            }
            "interaction_wait" => self.wait_for_person(&request).await,
            other => Err(format!("the relay does not know {other}")),
        }
    }

    /// `tools/list`, or a `tools/call` when `request_id` is given.
    ///
    /// A listing is asked again whenever it failed. A tool call has side
    /// effects, so it is sent again only where that cannot run it twice: to a
    /// Lemma that keeps each call's result by `request_id` (it says so in its
    /// `welcome`), or after a refusal that says nothing ran.
    async fn relay_mcp(
        &self,
        request: &RelayRequest,
        request_id: Option<String>,
    ) -> Result<Value, String> {
        let is_call = request_id.is_some();
        let mut attempts = 0;
        loop {
            attempts += 1;
            let (conversation_id, token) = self.run_identity(request.run_id)?;
            let link = self.current_link().await?;
            let once = !is_call || link.idempotent_tool_calls();
            let body = McpBody {
                run_id: request.run_id,
                conversation_id,
                token,
                method: request.method.clone(),
                params: request.params.clone(),
                request_id: request_id.clone(),
            };
            match link.mcp(&body).await {
                Ok(result) => return Ok(result),
                // A credential refused once is worth one more try: the relay
                // reads the run's token afresh every call, and a replacement
                // may be landing right now. Nothing ran, so this is safe for a
                // tool call too.
                Err(LinkError::Rejected { code, .. })
                    if code == "UNAUTHORIZED" && attempts == 1 =>
                {
                    tracing::info!(
                        "Lemma refused the run's credential; retrying with the journalled one"
                    );
                    tokio::time::sleep(RETRY_PAUSE).await;
                }
                // Lemma says it could not answer and that asking again is safe.
                // An older Lemma said so of a tool call it had already started,
                // so for a call that is believed only when Lemma keeps results.
                Err(LinkError::Rejected {
                    retryable: true,
                    message,
                    ..
                }) if once && attempts < MAX_ATTEMPTS => {
                    tracing::info!(%message, "Lemma could not answer an MCP request yet; retrying");
                    tokio::time::sleep(RETRY_PAUSE * attempts).await;
                }
                Err(LinkError::Rejected { message, .. }) => return Err(message),
                Err(error) if once && attempts < MAX_ATTEMPTS => {
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
            // on the next one. A run that ends -- stopped, failed, or its
            // agent gone -- ends the wait with it, rather than holding a
            // slot here and on Lemma for the rest of the half hour.
            let outcome = tokio::select! {
                outcome = tokio::time::timeout(remaining, link.interaction_wait(&body)) => outcome,
                () = self.run_ended(request.run_id) => {
                    return Err("the run ended while it waited for an answer".to_owned());
                }
            };
            match outcome {
                Ok(Ok(answer)) => return Ok(answer),
                Ok(Err(LinkError::Rejected { message, .. })) => return Err(message),
                Ok(Err(error)) => {
                    tracing::info!(%error, "the link dropped while waiting for a person; waiting again");
                }
                Err(_) => return Err("nobody answered in time".to_owned()),
            }
        }
    }

    /// Resolves once the journal says the run is over, or has forgotten it.
    async fn run_ended(&self, run_id: Uuid) {
        loop {
            tokio::time::sleep(RUN_CHECK).await;
            match self.journal.get_run(self.target_id, run_id) {
                Ok(Some(run)) if !run.state.is_terminal() => {}
                Ok(_) => return,
                // A journal read that failed says nothing about the run.
                Err(error) => tracing::debug!(%error, "could not read a waiting run's state"),
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::link::stub::StubLink;
    use crate::link::{self, LinkSlotOwner};
    use crate::protocol::{Command, CommandKind, HostCapacity, HostHello, JsonMap, RunSpec};
    use std::sync::atomic::Ordering;

    struct Fixture {
        relay: Relay,
        stub: StubLink,
        run_id: Uuid,
        _directory: tempfile::TempDir,
        _link: tokio::task::JoinHandle<()>,
    }

    /// A relay for one journalled run, over a link to the stand-in that is
    /// reopened whenever it drops, as the worker's link loop does.
    async fn fixture(idempotent: bool) -> Fixture {
        let stub = StubLink::start().await;
        stub.state
            .idempotent_tool_calls
            .store(idempotent, Ordering::SeqCst);
        let directory = tempfile::tempdir().unwrap();
        let journal = Journal::open(directory.path().join("journal.sqlite3")).unwrap();
        let target_id = Uuid::new_v4();
        let run_id = Uuid::new_v4();
        journal.register_target(target_id).unwrap();
        let spec = RunSpec {
            agent_run_id: run_id,
            conversation_id: Uuid::new_v4(),
            harness_id: Uuid::new_v4(),
            profile_revision: "relay".into(),
            model_name: None,
            config_selections: JsonMap::new(),
            system_prompt: String::new(),
            prompt: vec![serde_json::json!({"type": "text", "text": "hi"})],
            resume_session_id: None,
            workspace_cwd: None,
            context: JsonMap::new(),
            mcp: serde_json::json!({ "token": "run-token" }),
            run_deadline: chrono::Utc::now() + chrono::Duration::minutes(5),
            system_prompt_delivery: None,
        };
        let command = Command {
            command_id: Uuid::new_v4(),
            kind: CommandKind::StartRun,
            created_at: chrono::Utc::now(),
            expires_at: chrono::Utc::now() + chrono::Duration::minutes(5),
            run_id: Some(run_id),
            lease_epoch: Some(1),
            payload: serde_json::to_value(&spec).unwrap(),
        };
        journal
            .accept_start(target_id, &command, &spec, "cursor", "native-acp-1")
            .unwrap();
        let (owner, slot) = LinkSlotOwner::new();
        let url = stub.url.clone();
        let link = tokio::spawn(async move {
            loop {
                if let Ok(connected) = link::connect(
                    &url,
                    "secret",
                    HostHello::current("relay"),
                    HostCapacity::default(),
                )
                .await
                {
                    owner.set(Some(connected.handle.clone()));
                    connected.handle.closed().await;
                    owner.set(None);
                }
                tokio::time::sleep(Duration::from_millis(20)).await;
            }
        });
        Fixture {
            relay: Relay {
                target_id,
                journal,
                link: slot,
                token: "relay-token".into(),
            },
            stub,
            run_id,
            _directory: directory,
            _link: link,
        }
    }

    fn call(run_id: Uuid) -> RelayRequest {
        RelayRequest {
            id: 7,
            token: "relay-token".into(),
            run_id,
            method: "tools/call".into(),
            params: serde_json::json!({ "name": "lemma_display_resource", "arguments": {} }),
        }
    }

    fn sent_ids(stub: &StubLink) -> Vec<Value> {
        stub.state
            .mcp_requests
            .lock()
            .unwrap()
            .iter()
            .map(|body| body["request_id"].clone())
            .collect()
    }

    /// A Lemma that keeps each call's result by `request_id` is asked again
    /// after the link dropped under the call -- with the same id, so it
    /// answers from what it kept instead of running the tool a second time.
    #[tokio::test]
    async fn a_tool_call_is_sent_again_with_the_same_id_to_a_lemma_that_keeps_results() {
        let fixture = fixture(true).await;
        fixture
            .stub
            .state
            .drop_after_tool_calls
            .store(1, Ordering::SeqCst);
        let answered = fixture.relay.answer(call(fixture.run_id)).await;
        assert!(answered.is_ok(), "{answered:?}");
        let ids = sent_ids(&fixture.stub);
        assert_eq!(ids.len(), 2, "{ids:?}");
        assert!(ids[0].is_string());
        assert_eq!(ids[0], ids[1]);
    }

    /// An older Lemma ignores `request_id`, so a call the link dropped under
    /// is reported, not repeated: it may already have run.
    #[tokio::test]
    async fn a_tool_call_is_not_repeated_to_a_lemma_that_does_not_keep_results() {
        let fixture = fixture(false).await;
        fixture
            .stub
            .state
            .drop_after_tool_calls
            .store(1, Ordering::SeqCst);
        let answered = fixture.relay.answer(call(fixture.run_id)).await;
        let error = answered.expect_err("the call must not be sent twice");
        assert!(error.contains("may or may not have run"), "{error}");
        assert_eq!(sent_ids(&fixture.stub).len(), 1);
    }

    /// A run that has ended has no tools, whoever still holds its id.
    #[tokio::test]
    async fn a_run_that_ended_is_not_served() {
        let fixture = fixture(true).await;
        let run = fixture
            .relay
            .journal
            .get_run(fixture.relay.target_id, fixture.run_id)
            .unwrap()
            .unwrap();
        fixture
            .relay
            .journal
            .checkpoint(
                fixture.relay.target_id,
                fixture.run_id,
                run.lease_epoch,
                crate::protocol::RunState::Succeeded,
                &JsonMap::new(),
            )
            .unwrap();
        let error = fixture
            .relay
            .answer(call(fixture.run_id))
            .await
            .expect_err("an ended run is refused");
        assert!(error.contains("ended"), "{error}");
        assert!(sent_ids(&fixture.stub).is_empty());
    }

    #[tokio::test]
    async fn a_line_is_read_only_up_to_its_limit() {
        let mut reader = BufReader::new(&b"short\nlonger than eight\n"[..]);
        assert_eq!(
            read_line_bounded(&mut reader, 8).await.unwrap().as_deref(),
            Some("short")
        );
        assert!(read_line_bounded(&mut reader, 8).await.is_err());
        let mut empty = BufReader::new(&b""[..]);
        assert!(read_line_bounded(&mut empty, 8).await.unwrap().is_none());
    }

    #[test]
    fn an_answer_too_large_for_the_bridge_becomes_an_error_it_can_read() {
        let huge = RelayResponse {
            id: 3,
            result: Some(Value::String("x".repeat(MAX_RELAY_LINE + 1))),
            error: None,
        };
        let line = wire_line(&huge).unwrap();
        assert!(line.len() < 1024);
        let parsed: RelayResponse = serde_json::from_slice(&line).unwrap();
        assert_eq!(parsed.id, 3);
        assert!(parsed.error.unwrap().contains("bytes"));
    }

    /// A connection that never presents the token is let go, rather than
    /// held for as long as it likes.
    #[tokio::test]
    async fn a_silent_connection_is_let_go() {
        let fixture = fixture(true).await;
        let relay = Arc::new(fixture.relay);
        let listener = TcpListener::bind(("127.0.0.1", 0)).await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let serving = tokio::spawn(async move {
            let (stream, _) = listener.accept().await.unwrap();
            relay.connection(stream).await
        });
        let _silent = TcpStream::connect(("127.0.0.1", port)).await.unwrap();
        let ended = tokio::time::timeout(AUTH_DEADLINE * 2, serving)
            .await
            .expect("the relay must give up on a connection that says nothing")
            .unwrap();
        assert!(ended.is_err());
    }
}
