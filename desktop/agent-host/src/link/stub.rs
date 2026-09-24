//! A stand-in for Lemma's end of the link, for the crate's own tests.
//!
//! It answers every frame the host sends with the shape the backend does, and
//! lets a test script the parts that matter to it: which runs' events or
//! checkpoints to refuse, which commands to hand out, and when to push. It is
//! deliberately small: the behaviour a test depends on is written here where
//! it can be read, rather than inherited from a real server's defaults.

use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex};

use axum::Router;
use axum::extract::State;
use axum::extract::ws::{Message, WebSocket, WebSocketUpgrade};
use axum::response::Response;
use axum::routing::get;
use serde_json::{Value, json};
use tokio::net::TcpListener;
use tokio::sync::mpsc;
use uuid::Uuid;

use super::protocol::{
    ControlBody, Frame, HarnessesBody, PublishedHarness, close, host, server,
};
use crate::protocol::{Command, EventBatch, RunState};

#[derive(Default)]
pub(crate) struct StubState {
    /// Runs whose event batches are refused, as Lemma does when its transient
    /// event stream no longer holds the sequences a batch assumes.
    pub(crate) refused_runs: Mutex<Vec<Uuid>>,
    pub(crate) accepted: Mutex<Vec<(Uuid, u64)>>,
    /// Runs whose checkpoints are named in `control_ok.refused`.
    pub(crate) refused_checkpoints: Mutex<Vec<Uuid>>,
    pub(crate) applied_checkpoints: Mutex<Vec<(Uuid, RunState)>>,
    pub(crate) acknowledged: Mutex<Vec<Uuid>>,
    pub(crate) controls: Mutex<u32>,
    /// Commands handed out one per `control` answer.
    pub(crate) undelivered_commands: Mutex<VecDeque<Command>>,
    /// What an `mcp` request is answered with, by method.
    pub(crate) mcp_answers: Mutex<HashMap<String, Value>>,
    pub(crate) mcp_requests: Mutex<Vec<Value>>,
    /// What an `interaction_wait` is answered with, once set.
    pub(crate) interaction_answer: Mutex<Option<Value>>,
    /// Pushes to deliver to whichever host is connected.
    pushes: Mutex<Option<mpsc::UnboundedSender<Frame>>>,
    /// Close the next `hello` with this code instead of welcoming it.
    pub(crate) refuse_hello_with: Mutex<Option<u16>>,
    harness_ids: Mutex<HashMap<String, Uuid>>,
}

impl StubState {
    /// Push a frame to the connected host, if there is one.
    pub(crate) fn push(&self, kind: &str, body: Value) -> bool {
        self.pushes.lock().unwrap().as_ref().is_some_and(|pushes| {
            pushes
                .send(Frame {
                    kind: kind.to_owned(),
                    id: None,
                    re: None,
                    body,
                })
                .is_ok()
        })
    }

    pub(crate) fn connected(&self) -> bool {
        self.pushes
            .lock()
            .unwrap()
            .as_ref()
            .is_some_and(|pushes| !pushes.is_closed())
    }
}

pub(crate) struct StubLink {
    pub(crate) url: url::Url,
    pub(crate) state: Arc<StubState>,
    server: tokio::task::JoinHandle<()>,
}

impl StubLink {
    pub(crate) async fn start() -> Self {
        let state = Arc::<StubState>::default();
        let app = Router::new()
            .route("/agent-host/link", get(upgrade))
            .with_state(Arc::clone(&state));
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let port = listener.local_addr().unwrap().port();
        let server = tokio::spawn(async move {
            axum::serve(listener, app).await.unwrap();
        });
        Self {
            url: url::Url::parse(&format!("http://127.0.0.1:{port}")).unwrap(),
            state,
            server,
        }
    }

    /// Stop answering altogether, as an unreachable workspace does.
    pub(crate) fn stop(&self) {
        self.server.abort();
    }
}

impl Drop for StubLink {
    fn drop(&mut self) {
        self.server.abort();
    }
}

async fn upgrade(State(state): State<Arc<StubState>>, socket: WebSocketUpgrade) -> Response {
    socket.on_upgrade(move |socket| serve(state, socket))
}

fn reply(request: &Frame, kind: &str, body: Value) -> Frame {
    Frame {
        kind: kind.to_owned(),
        id: None,
        re: request.id.clone(),
        body,
    }
}

async fn serve(state: Arc<StubState>, mut socket: WebSocket) {
    let (push_tx, mut push_rx) = mpsc::unbounded_channel::<Frame>();
    loop {
        tokio::select! {
            push = push_rx.recv() => {
                let Some(push) = push else { continue };
                let text = serde_json::to_string(&push).unwrap();
                if socket.send(Message::Text(text.into())).await.is_err() {
                    return;
                }
            }
            message = socket.recv() => {
                let Some(Ok(message)) = message else { return };
                let Message::Text(text) = message else { continue };
                let frame: Frame = serde_json::from_str(&text).unwrap();
                let refusal = if frame.kind == host::HELLO {
                    state.refuse_hello_with.lock().unwrap().take()
                } else {
                    None
                };
                if let Some(code) = refusal {
                    let _ = socket
                        .send(Message::Close(Some(axum::extract::ws::CloseFrame {
                            code,
                            reason: "refused".into(),
                        })))
                        .await;
                    return;
                }
                if frame.kind == host::HELLO {
                    *state.pushes.lock().unwrap() = Some(push_tx.clone());
                }
                let Some(answer) = answer(&state, &frame) else { continue };
                let text = serde_json::to_string(&answer).unwrap();
                if socket.send(Message::Text(text.into())).await.is_err() {
                    return;
                }
                if frame.kind == host::PAIR || frame.kind == host::REVOKE {
                    let _ = socket
                        .send(Message::Close(Some(axum::extract::ws::CloseFrame {
                            code: close::NORMAL,
                            reason: "done".into(),
                        })))
                        .await;
                    return;
                }
            }
        }
    }
}

fn answer(state: &StubState, frame: &Frame) -> Option<Frame> {
    Some(match frame.kind.as_str() {
        host::HELLO => reply(
            frame,
            server::WELCOME,
            json!({
                "host_id": Uuid::new_v4(),
                "user_id": Uuid::new_v4(),
                "protocol_version": crate::PROTOCOL_VERSION,
                "heartbeat_ms": 20_000,
            }),
        ),
        host::PAIR => reply(
            frame,
            server::PAIRED,
            json!({
                "host_id": Uuid::new_v4(),
                "user_id": Uuid::new_v4(),
                "host_secret": "stub-secret",
            }),
        ),
        host::CONTROL => {
            *state.controls.lock().unwrap() += 1;
            let body: ControlBody = serde_json::from_value(frame.body.clone()).unwrap();
            let refused_runs = state.refused_checkpoints.lock().unwrap().clone();
            let mut refused = Vec::new();
            for checkpoint in &body.checkpoints {
                if refused_runs.contains(&checkpoint.run_id) {
                    refused.push(json!({
                        "kind": "checkpoint",
                        "run_id": checkpoint.run_id,
                        "reason": "refused by the stub",
                    }));
                } else {
                    state
                        .applied_checkpoints
                        .lock()
                        .unwrap()
                        .push((checkpoint.run_id, checkpoint.state));
                }
            }
            state
                .acknowledged
                .lock()
                .unwrap()
                .extend(body.acknowledged_command_ids.iter().copied());
            let command = state.undelivered_commands.lock().unwrap().pop_front();
            reply(
                frame,
                server::CONTROL_OK,
                json!({ "commands": command.into_iter().collect::<Vec<_>>(), "refused": refused }),
            )
        }
        host::EVENTS => {
            let batch: EventBatch = serde_json::from_value(frame.body.clone()).unwrap();
            let first = batch.events.first().expect("batches are never empty");
            if state.refused_runs.lock().unwrap().contains(&first.run_id) {
                // What the backend answers for `event sequence gap`.
                return Some(reply(
                    frame,
                    server::ERROR,
                    json!({ "code": "SEQUENCE_GAP", "message": "gap", "retryable": false }),
                ));
            }
            let last = batch.events.last().expect("batches are never empty");
            state
                .accepted
                .lock()
                .unwrap()
                .push((first.run_id, last.sequence));
            reply(
                frame,
                server::EVENTS_OK,
                json!({ "ack": {
                    "run_id": first.run_id,
                    "lease_epoch": first.lease_epoch,
                    "acked_through": last.sequence,
                }}),
            )
        }
        host::HARNESSES => {
            let body: HarnessesBody = serde_json::from_value(frame.body.clone()).unwrap();
            // One id per harness key for the life of the host, as the
            // backend's unique `(host_id, harness_key)` guarantees.
            let mut ids = state.harness_ids.lock().unwrap();
            let items: Vec<PublishedHarness> = body
                .harnesses
                .iter()
                .map(|snapshot| PublishedHarness {
                    id: *ids
                        .entry(snapshot.harness_key.clone())
                        .or_insert_with(Uuid::new_v4),
                    harness_key: snapshot.harness_key.clone(),
                    adapter_version: snapshot.adapter_version.clone(),
                    config_revision: snapshot.config_revision.clone(),
                })
                .collect();
            reply(frame, server::HARNESSES_OK, json!({ "items": items }))
        }
        host::MCP => {
            state.mcp_requests.lock().unwrap().push(frame.body.clone());
            let method = frame.body["method"].as_str().unwrap_or_default().to_owned();
            let result = state
                .mcp_answers
                .lock()
                .unwrap()
                .get(&method)
                .cloned()
                .unwrap_or_else(|| json!({}));
            reply(frame, server::MCP_OK, json!({ "result": result }))
        }
        host::INTERACTION_WAIT => {
            let answer = state.interaction_answer.lock().unwrap().clone()?;
            reply(frame, server::INTERACTION_OK, json!({ "answer": answer }))
        }
        host::REVOKE => reply(frame, server::REVOKED, json!({})),
        _ => return None,
    })
}
