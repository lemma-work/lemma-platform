//! Messages the person sends while a turn is already running.
//!
//! ACP's `session/prompt` is one request that returns when the turn ends, and
//! v1 has no method for adding input to it. The pinned adapters that Lemma
//! ships -- `claude-agent-acp` 0.62 and `codex-acp` 1.1 -- both implement the
//! same extension for it: a `_session/steering` request carrying `sessionId`
//! and `prompt`, advertised as `InitializeResponse._meta.steering.supported`.
//! It answers `{"outcome": "injected"}` when the message joined the running
//! turn, and `{"outcome": "startedNewTurn"}` when that turn had already ended
//! and the adapter started a fresh one with the message instead.
//!
//! Only `injected` counts as delivered here. A new turn the adapter started on
//! its own belongs to no Lemma run: this run's prompt has already returned,
//! its process goes with it, and whatever the turn wrote would land in a
//! transcript that is already over. So that turn is cancelled and the message
//! is reported as not delivered -- Lemma's follow-up turn carries it instead,
//! which is where it would have gone had the steer arrived a moment later.
//!
//! An adapter that does not advertise steering (`OpenCode`, as of its native ACP
//! at 1.18) never receives the request; every message for it is reported
//! undelivered at once and the follow-up turn answers it.

use futures_util::future::BoxFuture;
use serde_json::{Value, json};
use tokio::sync::mpsc;

use agent_client_protocol::schema::v1::{ContentBlock, Meta, SessionId};
use agent_client_protocol::{Agent, ConnectionTo, UntypedMessage};

use super::{AcpCallbacks, EventType, JsonMap, structured_block};
use crate::protocol::SteerResultPayload;

/// The extension method both steering adapters implement.
pub const STEER_METHOD: &str = "_session/steering";

/// Why a steer did not land, in the words Lemma records.
pub const STEER_UNSUPPORTED: &str = "unsupported";
pub const STEER_TURN_ENDED: &str = "turn_ended";

/// One message to add to the running turn.
#[derive(Clone, Debug)]
pub struct Steer {
    /// The Lemma message it carries, reported back as the result's
    /// `object_id`.
    pub message_id: String,
    pub prompt: Vec<ContentBlock>,
}

impl Steer {
    /// A `STEER_RUN` payload as the blocks the adapter will be sent.
    ///
    /// Text blocks travel as text; anything else goes through the same parser
    /// the prompt uses, so the two agree on what a block this build cannot
    /// represent turns into.
    #[must_use]
    pub fn from_payload(payload: &crate::protocol::SteerRunPayload) -> Self {
        let prompt = payload
            .prompt
            .iter()
            .filter_map(|value| match value.get("type").and_then(Value::as_str) {
                Some("text") => value.get("text").and_then(Value::as_str).map(|text| {
                    ContentBlock::Text(agent_client_protocol::schema::v1::TextContent::new(text))
                }),
                _ => structured_block(value),
            })
            .collect();
        Self {
            message_id: payload.message_id.clone(),
            prompt,
        }
    }
}

/// Where a run's steers wait until its turn can take them.
///
/// A run only reads it once its prompt is out, so a message that arrives while
/// the session is still being opened is held rather than lost -- and is sent
/// after the prompt, which is the order the adapters need to see a turn in
/// flight to add it to.
pub type SteerSender = mpsc::UnboundedSender<Steer>;

/// The receiving half, taken by exactly one turn.
///
/// Behind a lock only because `AcpRunRequest` is `Clone`; a run takes it once.
#[derive(Clone, Default)]
pub struct SteerInbox(std::sync::Arc<std::sync::Mutex<Option<mpsc::UnboundedReceiver<Steer>>>>);

impl SteerInbox {
    /// A connected pair: the runtime keeps the sender, the run gets this.
    #[must_use]
    pub fn channel() -> (SteerSender, Self) {
        let (sender, receiver) = mpsc::unbounded_channel();
        (
            sender,
            Self(std::sync::Arc::new(std::sync::Mutex::new(Some(receiver)))),
        )
    }

    pub(crate) fn take(&self) -> Option<mpsc::UnboundedReceiver<Steer>> {
        self.0.lock().expect("steer inbox poisoned").take()
    }
}

/// Whether an agent's `initialize` answer advertised `_session/steering`.
#[must_use]
pub fn steering_advertised(meta: Option<&Meta>) -> bool {
    meta.and_then(|meta| meta.get("steering"))
        .and_then(|steering| steering.get("supported"))
        == Some(&Value::Bool(true))
}

/// What a turn's steering asks the turn to do next.
pub(crate) enum SteerEvent {
    Arrived(Steer),
    Answered(String, Result<Value, agent_client_protocol::Error>),
}

type InFlight = BoxFuture<'static, (String, Result<Value, agent_client_protocol::Error>)>;

/// The steering half of one turn: what came in, what is out, what landed.
pub(crate) struct TurnSteering<'a> {
    supported: bool,
    inbox: Option<mpsc::UnboundedReceiver<Steer>>,
    callbacks: &'a dyn AcpCallbacks,
    in_flight: futures_util::stream::FuturesUnordered<InFlight>,
    /// The messages whose answers are still out, by id. The futures above
    /// are opaque until they resolve, and the turn can end first.
    awaiting: std::collections::HashSet<String>,
}

impl<'a> TurnSteering<'a> {
    pub(crate) fn new(
        supported: bool,
        inbox: Option<mpsc::UnboundedReceiver<Steer>>,
        callbacks: &'a dyn AcpCallbacks,
    ) -> Self {
        Self {
            supported,
            inbox,
            callbacks,
            in_flight: futures_util::stream::FuturesUnordered::new(),
            awaiting: std::collections::HashSet::new(),
        }
    }

    /// The next thing to act on: a message Lemma wants added, or an
    /// adapter's answer to one already sent. Never resolves while neither can
    /// happen, so it sits harmlessly beside the turn in a `select!`.
    pub(crate) async fn next(&mut self) -> SteerEvent {
        use futures_util::StreamExt;
        loop {
            let Self {
                inbox,
                in_flight,
                awaiting,
                ..
            } = &mut *self;
            tokio::select! {
                // Answers before arrivals, so what the agent said is recorded
                // before anything more is sent to it.
                biased;
                Some((message_id, answer)) = in_flight.next(), if !in_flight.is_empty() => {
                    awaiting.remove(&message_id);
                    return SteerEvent::Answered(message_id, answer);
                }
                steer = async {
                    match inbox.as_mut() {
                        Some(receiver) => receiver.recv().await,
                        None => std::future::pending().await,
                    }
                } => {
                    if let Some(steer) = steer {
                        return SteerEvent::Arrived(steer);
                    }
                    // The runtime dropped its sender, so nothing more is
                    // coming; wait on answers alone from here.
                    *inbox = None;
                }
            }
        }
    }

    /// Send one message into the turn, or say at once that it cannot go.
    pub(crate) fn send(
        &mut self,
        connection: &ConnectionTo<Agent>,
        session_id: &SessionId,
        steer: Steer,
    ) {
        if !self.supported {
            self.report(&steer.message_id, false, Some(STEER_UNSUPPORTED));
            return;
        }
        let request = match UntypedMessage::new(
            STEER_METHOD,
            json!({ "sessionId": session_id.to_string(), "prompt": steer.prompt }),
        ) {
            Ok(request) => request,
            Err(error) => {
                self.report(&steer.message_id, false, Some(&error.to_string()));
                return;
            }
        };
        let response = connection.send_request(request).block_task();
        let message_id = steer.message_id;
        self.awaiting.insert(message_id.clone());
        self.in_flight
            .push(Box::pin(async move { (message_id, response.await) }));
    }

    /// Record what an adapter said about one steer.
    ///
    /// Returns whether the adapter started a turn of its own, which the caller
    /// has to cancel: see the module documentation.
    pub(crate) fn settle(
        &self,
        message_id: &str,
        answer: Result<Value, agent_client_protocol::Error>,
    ) -> bool {
        match answer {
            Ok(value) => match value.get("outcome").and_then(Value::as_str) {
                Some("injected") => {
                    self.report(message_id, true, None);
                    false
                }
                Some("startedNewTurn") => {
                    self.report(message_id, false, Some(STEER_TURN_ENDED));
                    true
                }
                other => {
                    self.report(
                        message_id,
                        false,
                        Some(&format!("unexpected steering outcome {other:?}")),
                    );
                    false
                }
            },
            Err(error) => {
                self.report(message_id, false, Some(&error.to_string()));
                false
            }
        }
    }

    /// The turn is over: nothing still waiting can reach it any more.
    ///
    /// Reported rather than left silent so Lemma can say why the message went
    /// to the next turn instead; Lemma's follow-up carries it either way.
    pub(crate) fn finish(mut self) {
        use futures_util::{FutureExt, StreamExt};
        // Answers that already arrived still count: the turn and a steer can
        // resolve together, and a cancel can end the loop with one in hand.
        while let Some(Some((message_id, answer))) = self.in_flight.next().now_or_never() {
            self.awaiting.remove(&message_id);
            // A turn the adapter started now dies with this run's process.
            let _ = self.settle(&message_id, answer);
        }
        let mut abandoned = Vec::new();
        if let Some(inbox) = self.inbox.as_mut() {
            while let Ok(steer) = inbox.try_recv() {
                abandoned.push(steer.message_id);
            }
        }
        // Dropping the futures drops the waits on their answers. The adapter
        // may still act on one, but only by starting a turn this run's process
        // does not outlive.
        abandoned.extend(self.awaiting.drain());
        for message_id in abandoned {
            self.report(&message_id, false, Some(STEER_TURN_ENDED));
        }
    }

    fn report(&self, message_id: &str, delivered: bool, detail: Option<&str>) {
        let payload = SteerResultPayload {
            delivered,
            detail: detail.map(str::to_owned),
        };
        let payload = match serde_json::to_value(payload) {
            Ok(Value::Object(map)) => map.into_iter().collect(),
            _ => JsonMap::new(),
        };
        if delivered {
            tracing::info!(message_id, "steered a message into the running turn");
        } else {
            tracing::info!(message_id, detail, "a steer did not reach the running turn");
        }
        if let Err(error) =
            self.callbacks
                .event(EventType::SteerResult, Some(message_id.to_owned()), payload)
        {
            tracing::error!(%error, "could not record a steering result");
        }
    }
}
