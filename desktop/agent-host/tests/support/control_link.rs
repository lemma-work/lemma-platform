//! The control plane's end of the link.
//!
//! One WebSocket per host at `/agent-host/link`, as
//! `app/modules/agent/domain/agent_host_link.py` serves it: `pair` or an
//! authenticated `hello` first, then `control`, `events`, `harnesses`, `mcp`
//! and `interaction_wait` requests, each answered by a frame whose `re` names
//! it, and `commands` pushed whenever there is work.
//!
//! The push is the part the host now depends on. It no longer polls: nothing
//! arrives unless this side sends it, so a stand-in that only answered
//! `control` frames would starve the host between heartbeats. Every
//! connection therefore gets a pusher that re-evaluates what is owed every
//! [`PUSH_INTERVAL`] and straight after each `events` and `harnesses` frame,
//! the same moments Lemma's poke and its 5-second floor cover.

use super::*;

use axum::extract::ws::{CloseFrame, Message, WebSocket, WebSocketUpgrade};
use futures_util::stream::SplitStream;
use futures_util::{SinkExt, StreamExt};
use lemma_agent_host::link::protocol::{Frame, close, host, server};
use tokio::sync::{Notify, mpsc};

/// How often a connection's pusher looks for commands the host is owed.
const PUSH_INTERVAL: Duration = Duration::from_millis(50);
/// How often an unacknowledged `START_RUN` is pushed again on one link.
///
/// Redelivery is the point -- see `ControlState::start_command` -- but pushing
/// it every 50ms would bury a host that is still journaling the first copy.
const START_REOFFER: Duration = Duration::from_secs(1);

pub(crate) fn assistant_text_of(events: &[Event]) -> String {
    events
        .iter()
        .filter(|event| event.event_type == EventType::AgentMessageChunk)
        .filter_map(|event| event.payload.get("text").and_then(Value::as_str))
        .collect()
}

pub(crate) fn header(headers: &HeaderMap, name: &str) -> Option<String> {
    headers
        .get(name)
        .and_then(|value| value.to_str().ok())
        .map(str::to_owned)
}

/// Accept the upgrade whatever it carries, and judge the credential once the
/// first frame says what the connection is for. That is the backend's order
/// too: a refusal is a close code the host can read, not an HTTP status the
/// WebSocket client reports as a failed handshake.
pub(crate) async fn link(
    State(state): State<ControlState>,
    headers: HeaderMap,
    upgrade: WebSocketUpgrade,
) -> Response {
    let authorization = header(&headers, "authorization");
    upgrade.on_upgrade(move |socket| session(state, socket, authorization))
}

enum Outgoing {
    Frame(Frame),
    Close(u16, &'static str),
}

/// One connection's way of answering: every frame goes through one writer,
/// so answers and pushes are never interleaved on the socket.
#[derive(Clone)]
struct Session {
    out: mpsc::UnboundedSender<Outgoing>,
    /// Wakes the pusher early: something may now be owed.
    wake: Arc<Notify>,
    /// Set once this side has decided to close; nothing further is handled.
    closing: Arc<AtomicBool>,
}

impl Session {
    fn send(&self, frame: Frame) {
        let _ = self.out.send(Outgoing::Frame(frame));
    }

    fn reply(&self, request: &Frame, kind: &str, body: Value) {
        self.send(Frame {
            kind: kind.to_owned(),
            id: None,
            re: request.id.clone(),
            body,
        });
    }

    fn error(&self, request: &Frame, code: &str, message: &str, retryable: bool) {
        self.reply(
            request,
            server::ERROR,
            json!({ "code": code, "message": message, "retryable": retryable }),
        );
    }

    fn close(&self, code: u16, reason: &'static str) {
        self.closing.store(true, Ordering::SeqCst);
        let _ = self.out.send(Outgoing::Close(code, reason));
    }

    /// The answer the host never receives. Whatever the request did has been
    /// committed; the link goes before the answer does, which is what a
    /// restarting replica or a dropped connection looks like from the host.
    fn drop_link(&self) {
        self.close(close::RESTARTING, "the stand-in dropped the link");
    }
}

async fn next_frame(stream: &mut SplitStream<WebSocket>) -> Option<Frame> {
    loop {
        match stream.next().await? {
            Ok(Message::Text(text)) => {
                return Some(serde_json::from_str(&text).expect("the host sent a frame"));
            }
            Ok(Message::Close(_)) | Err(_) => return None,
            Ok(_) => {}
        }
    }
}

async fn session(state: ControlState, socket: WebSocket, authorization: Option<String>) {
    let (mut sink, mut stream) = socket.split();
    let (out, mut outgoing) = mpsc::unbounded_channel::<Outgoing>();
    tokio::spawn(async move {
        while let Some(next) = outgoing.recv().await {
            match next {
                Outgoing::Frame(frame) => {
                    let text = serde_json::to_string(&frame).unwrap();
                    if sink.send(Message::Text(text.into())).await.is_err() {
                        break;
                    }
                }
                Outgoing::Close(code, reason) => {
                    let _ = sink
                        .send(Message::Close(Some(CloseFrame {
                            code,
                            reason: reason.into(),
                        })))
                        .await;
                    break;
                }
            }
        }
    });
    let session = Session {
        out,
        wake: Arc::new(Notify::new()),
        closing: Arc::new(AtomicBool::new(false)),
    };

    // The first frame decides what this connection is.
    let Some(first) = next_frame(&mut stream).await else {
        return;
    };
    match first.kind.as_str() {
        // Pairing is the one exchange without a host secret: it is the one
        // that issues it. Answered, then closed.
        host::PAIR => {
            state.pairings.lock().unwrap().push(first.body.clone());
            session.reply(
                &first,
                server::PAIRED,
                json!({
                    "host_id": state.host_id,
                    "user_id": state.user_id,
                    "host_secret": HOST_SECRET,
                }),
            );
            session.close(close::NORMAL, "paired");
        }
        host::HELLO => {
            if authorization.as_deref() != Some(format!("Bearer {HOST_SECRET}").as_str()) {
                session.close(
                    close::INVALID_CREDENTIAL,
                    "missing or malformed host secret",
                );
                return;
            }
            if let Some(code) = state.refuse_next_hello_with.lock().unwrap().take() {
                session.close(code, "refused by the test");
                return;
            }
            state.hellos.lock().unwrap().push(first.body.clone());
            session.reply(
                &first,
                server::WELCOME,
                json!({
                    "host_id": state.host_id,
                    "user_id": state.user_id,
                    "protocol_version": lemma_agent_host::PROTOCOL_VERSION,
                    "heartbeat_ms": 20_000,
                }),
            );
            let pusher = tokio::spawn(push_commands(state.clone(), session.clone()));
            while !session.closing.load(Ordering::SeqCst) {
                let Some(frame) = next_frame(&mut stream).await else {
                    break;
                };
                handle(&state, &session, frame);
            }
            pusher.abort();
        }
        _ => session.close(
            close::PROTOCOL_VIOLATION,
            "the first frame must be pair or hello",
        ),
    }
    // Ends the writer if the host is still there to be told.
    let _ = session.out.send(Outgoing::Close(close::NORMAL, "done"));
}

/// Push whatever the host is owed, for as long as the link is open.
async fn push_commands(state: ControlState, session: Session) {
    let mut start_offered_at: Option<tokio::time::Instant> = None;
    loop {
        tokio::select! {
            () = tokio::time::sleep(PUSH_INTERVAL) => {}
            () = session.wake.notified() => {}
        }
        if session.out.is_closed() || session.closing.load(Ordering::SeqCst) {
            return;
        }
        let offer_start = start_offered_at.is_none_or(|offered| offered.elapsed() >= START_REOFFER);
        let commands = owed_commands(&state, offer_start);
        if commands.is_empty() {
            continue;
        }
        if commands
            .iter()
            .any(|command| command["kind"] == "START_RUN")
        {
            start_offered_at = Some(tokio::time::Instant::now());
        }
        if state
            .drop_link_on_first_command
            .swap(false, Ordering::SeqCst)
        {
            session.drop_link();
            return;
        }
        session.send(Frame {
            kind: server::COMMANDS.to_owned(),
            id: None,
            re: None,
            body: json!({ "commands": commands }),
        });
    }
}

fn handle(state: &ControlState, session: &Session, frame: Frame) {
    match frame.kind.as_str() {
        host::CONTROL => control(state, session, &frame),
        host::EVENTS => {
            append_events(state, session, &frame);
            session.wake.notify_one();
        }
        host::HARNESSES => {
            publish(state, session, &frame);
            session.wake.notify_one();
        }
        host::MCP => {
            let endpoint = state.mcp_endpoint.lock().unwrap().clone();
            let session = session.clone();
            tokio::spawn(async move {
                let Some(endpoint) = endpoint else {
                    session.error(
                        &frame,
                        "NOT_FOUND",
                        "this test serves no Lemma tools",
                        false,
                    );
                    return;
                };
                match endpoint.answer(&frame.body) {
                    Ok(result) => {
                        session.reply(&frame, server::MCP_OK, json!({ "result": result }))
                    }
                    Err(failure) => refuse(&session, &frame, failure),
                }
            });
        }
        host::INTERACTION_WAIT => {
            let endpoint = state.mcp_endpoint.lock().unwrap().clone();
            let session = session.clone();
            // Held open until the person decides, so it must not hold up the
            // frames behind it.
            tokio::spawn(async move {
                let Some(endpoint) = endpoint else {
                    session.error(
                        &frame,
                        "NOT_FOUND",
                        "this test serves no Lemma tools",
                        false,
                    );
                    return;
                };
                match endpoint.wait_for_decision(&frame.body).await {
                    Ok(answer) => {
                        session.reply(&frame, server::INTERACTION_OK, json!({ "answer": answer }));
                    }
                    Err(failure) => refuse(&session, &frame, failure),
                }
            });
        }
        host::REVOKE => {
            *state.revocations.lock().unwrap() += 1;
            session.reply(&frame, server::REVOKED, json!({}));
            session.close(close::NORMAL, "revoked");
        }
        _ => session.error(
            &frame,
            "INVALID_FRAME",
            "not a frame a paired host sends",
            false,
        ),
    }
}

/// Deliver one of the MCP stand-in's failures the way Lemma would.
fn refuse(session: &Session, request: &Frame, failure: McpFailure) {
    match failure {
        McpFailure::Refused {
            code,
            message,
            retryable,
        } => session.error(request, code, &message, retryable),
        McpFailure::DropLink => session.drop_link(),
    }
}

fn control(state: &ControlState, session: &Session, frame: &Frame) {
    let body = &frame.body;
    // Kept, not ignored. See `ControlState::rejections`.
    if let Some(rejections) = body.get("rejections").and_then(Value::as_array)
        && !rejections.is_empty()
    {
        state
            .rejections
            .lock()
            .unwrap()
            .extend(rejections.iter().cloned());
        // A permanent refusal settles the command, as it does in Lemma, which
        // fails the run rather than offering it again.
        if let Some(offered) = state.start_command.lock().unwrap().as_ref()
            && rejections.iter().any(|rejection| {
                rejection["command_id"].as_str() == Some(offered.command_id.to_string().as_str())
                    && rejection["retryable"] == false
            })
        {
            state.start_sent.store(true, Ordering::SeqCst);
        }
    }
    // Command acknowledgements, which is how a real control plane learns a
    // command landed. Without reading these the stub cannot tell "delivered"
    // from "written into a frame that never arrived".
    let acked: Vec<Uuid> = body
        .get("acknowledged_command_ids")
        .and_then(Value::as_array)
        .map(|ids| {
            ids.iter()
                .filter_map(|id| id.as_str())
                .filter_map(|id| Uuid::parse_str(id).ok())
                .collect()
        })
        .unwrap_or_default();
    if !acked.is_empty()
        && let Some(offered) = state.start_command.lock().unwrap().as_ref()
        && acked.contains(&offered.command_id)
    {
        state.start_sent.store(true, Ordering::SeqCst);
    }
    let commands = owed_commands(state, true);
    if !commands.is_empty()
        && state
            .drop_link_on_first_command
            .swap(false, Ordering::SeqCst)
    {
        // The answer the host never received. A real one is lost to a dropped
        // connection or a replica restarting; the effect is the same, and the
        // command has to be offered again.
        session.drop_link();
        return;
    }
    session.reply(
        frame,
        server::CONTROL_OK,
        json!({ "commands": commands, "refused": [] }),
    );
}

/// The commands the host is owed right now.
///
/// `START_RUN` is offered until acknowledged, whenever `offer_start` allows;
/// the rest are one-shot, and selecting one marks it sent.
fn owed_commands(state: &ControlState, offer_start: bool) -> Vec<Value> {
    let mut commands = Vec::new();
    let published = state.published.lock().unwrap().clone();

    if let Some((harness_id, revision)) = published
        && offer_start
        && !state.start_sent.load(Ordering::SeqCst)
    {
        // Redelivery preserves the whole command, including its conversation
        // binding and deadline. Rebuilding those fields changes the work.
        let command = state
            .start_command
            .lock()
            .unwrap()
            .get_or_insert_with(|| Command {
                command_id: Uuid::new_v4(),
                kind: CommandKind::StartRun,
                created_at: Utc::now(),
                expires_at: Utc::now() + chrono::Duration::minutes(2),
                run_id: Some(state.run_id),
                lease_epoch: Some(1),
                payload: serde_json::to_value(RunSpec {
                    agent_run_id: state.run_id,
                    conversation_id: Uuid::new_v4(),
                    harness_id,
                    profile_revision: revision,
                    model_name: None,
                    config_selections: JsonMap::new(),
                    system_prompt: "Follow the runtime instructions exactly.".to_owned(),
                    prompt: vec![json!({"type": "text", "text": state.prompt})],
                    resume_session_id: None,
                    workspace_cwd: state.workspace_cwd.lock().unwrap().clone(),
                    context: BTreeMap::new(),
                    mcp: state.mcp.clone(),
                    run_deadline: Utc::now() + *state.run_budget.lock().unwrap(),
                    system_prompt_delivery: None,
                })
                .unwrap(),
            })
            .clone();
        commands.push(serde_json::to_value(command).unwrap());
    }
    let cancel_after = state.cancel_after.lock().unwrap().clone();
    if let Some(marker) = cancel_after {
        let seen = assistant_text_of(&state.events.lock().unwrap()).contains(&marker);
        if seen && !state.cancel_sent.swap(true, Ordering::SeqCst) {
            commands.push(json!({
                "command_id": Uuid::new_v4(),
                "kind": "CANCEL_RUN",
                "created_at": Utc::now(),
                "expires_at": Utc::now() + chrono::Duration::minutes(2),
                "run_id": state.run_id,
                "lease_epoch": 1,
                "payload": {"agent_run_id": state.run_id},
            }));
        }
    }
    let refresh_after = state.refresh_after.lock().unwrap().clone();
    if let Some((marker, mcp)) = refresh_after {
        let seen = assistant_text_of(&state.events.lock().unwrap()).contains(&marker);
        if seen && !state.refresh_sent.swap(true, Ordering::SeqCst) {
            commands.push(json!({
                "command_id": Uuid::new_v4(),
                "kind": "REFRESH_CREDENTIAL",
                "created_at": Utc::now(),
                "expires_at": Utc::now() + chrono::Duration::minutes(2),
                "run_id": state.run_id,
                "lease_epoch": 1,
                "payload": {"mcp": mcp},
            }));
        }
    }
    // Answer every parked request, not just the first: a real agent asks again
    // for each tool it wants, and a control plane that answered once would
    // leave the second request to time out.
    if !matches!(state.permission_answer, PermissionAnswer::Ignore) {
        let mut parked = state
            .events
            .lock()
            .unwrap()
            .iter()
            .filter(|event| event.event_type == EventType::PermissionRequest)
            .filter_map(|event| {
                event
                    .object_id
                    .clone()
                    .map(|request_id| (request_id, event.payload.clone()))
            })
            .collect::<Vec<_>>();
        if matches!(state.permission_answer, PermissionAnswer::AllowThenDeny) && parked.len() < 2 {
            // A concurrency test must observe both requests before answering
            // either. Immediate answers also pass a serial, blocked receiver.
            parked.clear();
        }
        let mut answered = state.answered.lock().unwrap();
        for (request_id, payload) in parked {
            let index = answered.len();
            if !answered.insert(request_id.clone()) {
                continue;
            }
            let option_id = state
                .permission_answer
                .option_for(index, &payload)
                .map_or(Value::Null, Value::String);
            let events = state.events.lock().unwrap();
            state.decisions.lock().unwrap().push(DecisionSnapshot {
                request_id: request_id.clone(),
                option_id: option_id.as_str().map(str::to_owned),
                assistant_text: assistant_text_of(&events),
                saw_terminal: events
                    .iter()
                    .any(|event| event.event_type == EventType::Terminal),
            });
            drop(events);
            commands.push(json!({
                "command_id": Uuid::new_v4(),
                "kind": "RESOLVE_PERMISSION",
                "created_at": Utc::now(),
                "expires_at": Utc::now() + chrono::Duration::minutes(2),
                "run_id": state.run_id,
                "lease_epoch": 1,
                "payload": {"request_id": request_id, "option_id": option_id},
            }));
        }
    }
    commands
}

fn publish(state: &ControlState, session: &Session, frame: &Frame) {
    let snapshots = frame.body["harnesses"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    state.snapshots.lock().unwrap().clone_from(&snapshots);
    let items = snapshots
        .iter()
        .map(|snapshot| {
            // One id per harness key, for the life of this control plane. See
            // `ControlState::harness_ids` for what minting a fresh one per
            // publish cost.
            let id = *state
                .harness_ids
                .lock()
                .unwrap()
                .entry(
                    snapshot["harness_key"]
                        .as_str()
                        .unwrap_or_default()
                        .to_owned(),
                )
                .or_insert_with(Uuid::new_v4);
            if snapshot["harness_key"].as_str() == Some(state.harness_key.as_str())
                && snapshot["health"].as_str() == Some("READY")
            {
                *state.published.lock().unwrap() = Some((
                    id,
                    snapshot["config_revision"]
                        .as_str()
                        .unwrap_or("")
                        .to_owned(),
                ));
            }
            json!({
                "id": id,
                "harness_key": snapshot["harness_key"],
                "adapter_version": snapshot["adapter_version"],
                "config_revision": snapshot["config_revision"],
            })
        })
        .collect::<Vec<_>>();
    session.reply(frame, server::HARNESSES_OK, json!({ "items": items }));
}

fn append_events(state: &ControlState, session: &Session, frame: &Frame) {
    let Ok(batch) = serde_json::from_value::<EventBatch>(frame.body.clone()) else {
        session.error(frame, "INVALID_FRAME", "not an event batch", false);
        return;
    };
    if batch.events.is_empty()
        || batch
            .events
            .iter()
            .any(|event| event.run_id != state.run_id || event.lease_epoch != 1)
    {
        session.error(frame, "STALE_LEASE", "not this run's current lease", false);
        return;
    }
    state
        .append_attempts
        .lock()
        .unwrap()
        .push(batch.events.iter().map(|event| event.sequence).collect());
    let mut events = state.events.lock().unwrap();
    let mut watermark = events.last().map_or(0, |event| event.sequence);
    for event in &batch.events {
        if event.sequence > watermark + 1 {
            // What the backend answers for a batch that skips a sequence: the
            // whole batch is refused before any of it lands.
            drop(events);
            session.error(frame, "SEQUENCE_GAP", "event sequence gap", false);
            return;
        }
        watermark = watermark.max(event.sequence);
    }
    for event in batch.events {
        if events
            .last()
            .is_none_or(|last| event.sequence > last.sequence)
        {
            events.push(event);
        }
    }
    drop(events);
    // An answer can be lost after the receiver committed the batch. Retries
    // must preserve the first event for a sequence, just as the backend does.
    if state
        .drop_link_after_first_append
        .swap(false, Ordering::SeqCst)
    {
        session.drop_link();
        return;
    }
    session.reply(
        frame,
        server::EVENTS_OK,
        json!({ "ack": {
            "run_id": state.run_id,
            "lease_epoch": 1,
            "acked_through": watermark,
        }}),
    );
}
