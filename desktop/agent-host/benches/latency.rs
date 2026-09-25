//! Where a turn's wall clock goes between Lemma and a real local agent.
//!
//! Not a correctness test — a measurement. It stands Lemma's end of the link
//! in front of the real `HostRuntime` and timestamps every hop, in two modes:
//!
//! * `turn` — a real agent answers one prompt. Lemma's side pushes the
//!   `START_RUN` the moment the run is dispatched, as the backend's poke does,
//!   and every frame the host sends back is stamped on arrival: the
//!   acknowledgement, each `events` batch, the first assistant text, the
//!   terminal event. Needs `LEMMA_REAL_AGENT_HOST_DATA_DIR`.
//! * `link` — the link's own cost, with no agent: round trips of `control`
//!   and `events` frames through `link::connect`, the two requests a streamed
//!   turn is made of. Needs nothing.
//!
//! It measures the host's own hops only. "The user sent a message" is a
//! dispatch simulated inside this bench, so nothing here covers the backend
//! pipeline a real message travels first.
//!
//! ```text
//! LEMMA_REAL_AGENT_HOST_DATA_DIR=... cargo bench -p lemma-agent-host
//! cargo bench -p lemma-agent-host -- link
//! ```
//!
//! A bench target, not a test one. It asserts nothing, so `cargo test` would
//! gain nothing from it and pay for it anyway: an integration test is its own
//! binary to compile and link, on every run, in every lane.

use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use axum::Router;
use axum::extract::State;
use axum::extract::ws::{CloseFrame, Message, WebSocket, WebSocketUpgrade};
use axum::http::HeaderMap;
use axum::response::Response;
use axum::routing::get;
use chrono::Utc;
use futures_util::{SinkExt, StreamExt};
use lemma_agent_host::config::HostPaths;
use lemma_agent_host::link::protocol::{ControlBody, Frame, close, host, server};
use lemma_agent_host::protocol::{
    Command, CommandKind, Event, EventBatch, EventType, HostCapacity, HostHello, JsonMap, RunSpec,
};
use serde_json::{Value, json};
use tempfile::TempDir;
use tokio::net::TcpListener;
use tokio::sync::{Notify, mpsc};
use uuid::Uuid;

// The shared helpers live beside the integration tests, and a bench target
// cannot reach a sibling target's modules -- so this names the module by path
// rather than duplicating it. Only its MCP stand-in is used: the agent's calls
// to Lemma's tools travel the link now, and answering them is part of a turn.
//
// The allowance is for borrowing it, not for the module. `use super::*` inside
// a test helper is idiomatic and clippy says nothing about it there; pulled
// into a bench target it becomes an ordinary wildcard import under
// `-D warnings`, and rewriting a shared test helper to suit a benchmark that
// borrows it is the wrong way round.
#[allow(clippy::wildcard_imports)]
#[path = "../tests/support/mod.rs"]
mod support;

const HOST_SECRET: &str = "latency-bench-host-secret-with-entropy";
/// How often an unacknowledged `START_RUN` is pushed again, as Lemma
/// redelivers a command until a `control` frame acknowledges it.
const START_REOFFER: Duration = Duration::from_secs(1);
/// Round trips per request kind in `link` mode.
const LINK_ROUND_TRIPS: usize = 500;

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Turn,
    Link,
}

/// One hop, stamped when it happened.
#[derive(Clone, Debug)]
struct Hop {
    at: Duration,
    what: String,
}

#[derive(Clone)]
struct BenchState {
    started: Instant,
    host_id: Uuid,
    user_id: Uuid,
    run_id: Uuid,
    harness_key: String,
    prompt: String,
    mcp: Value,
    mcp_endpoint: Option<support::LemmaMcpEndpoint>,
    published: Arc<Mutex<Option<(Uuid, String)>>>,
    /// One id per harness key, as Lemma's upsert keeps it.
    harness_ids: Arc<Mutex<HashMap<String, Uuid>>>,
    /// Set by the bench when it "sends the message", i.e. dispatches the run.
    dispatched: Arc<AtomicBool>,
    /// Wakes every connection's pusher: the dispatch is Lemma's poke.
    poke: Arc<Notify>,
    /// The `START_RUN`, built once and redelivered until acknowledged.
    start_command: Arc<Mutex<Option<Command>>>,
    start_acknowledged: Arc<AtomicBool>,
    hops: Arc<Mutex<Vec<Hop>>>,
    events: Arc<Mutex<Vec<Event>>>,
    /// Every frame the host sent, by kind, with when it arrived. What Lemma
    /// pays per turn: the link replaced a poll per wake and a POST per event.
    frames: Arc<Mutex<Vec<(Duration, String)>>>,
}

impl BenchState {
    fn new(harness: &str, prompt: &str, mcp_endpoint: Option<support::LemmaMcpEndpoint>) -> Self {
        Self {
            started: Instant::now(),
            host_id: Uuid::new_v4(),
            user_id: Uuid::new_v4(),
            run_id: Uuid::new_v4(),
            harness_key: harness.to_owned(),
            prompt: prompt.to_owned(),
            mcp: mcp_endpoint
                .as_ref()
                .map_or(Value::Null, support::LemmaMcpEndpoint::run_configuration),
            mcp_endpoint,
            published: Arc::default(),
            harness_ids: Arc::default(),
            dispatched: Arc::default(),
            poke: Arc::default(),
            start_command: Arc::default(),
            start_acknowledged: Arc::default(),
            hops: Arc::default(),
            events: Arc::default(),
            frames: Arc::default(),
        }
    }

    fn mark(&self, what: impl Into<String>) {
        self.hops.lock().unwrap().push(Hop {
            at: self.started.elapsed(),
            what: what.into(),
        });
    }
}

// ---------------------------------------------------------------------------
// Lemma's end of the link, stamping what crosses it.
// ---------------------------------------------------------------------------

async fn upgrade(
    State(state): State<BenchState>,
    headers: HeaderMap,
    socket: WebSocketUpgrade,
) -> Response {
    let authorized = headers
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        == Some(format!("Bearer {HOST_SECRET}").as_str());
    socket.on_upgrade(move |socket| session(state, socket, authorized))
}

enum Outgoing {
    Frame(Frame),
    Close(u16, &'static str),
}

fn reply(request: &Frame, kind: &str, body: Value) -> Outgoing {
    Outgoing::Frame(Frame {
        kind: kind.to_owned(),
        id: None,
        re: request.id.clone(),
        body,
    })
}

async fn session(state: BenchState, socket: WebSocket, authorized: bool) {
    let (mut sink, mut stream) = socket.split();
    let (out, mut outgoing) = mpsc::unbounded_channel::<Outgoing>();
    // One writer, so answers and pushes never interleave on the socket.
    tokio::spawn(async move {
        while let Some(next) = outgoing.recv().await {
            let message = match next {
                Outgoing::Frame(frame) => {
                    Message::Text(serde_json::to_string(&frame).unwrap().into())
                }
                Outgoing::Close(code, reason) => Message::Close(Some(CloseFrame {
                    code,
                    reason: reason.into(),
                })),
            };
            let closing = matches!(message, Message::Close(_));
            if sink.send(message).await.is_err() || closing {
                break;
            }
        }
    });
    let mut pusher = None;
    while let Some(Ok(message)) = stream.next().await {
        let Message::Text(text) = message else {
            continue;
        };
        let frame: Frame = serde_json::from_str(&text).expect("the host sent a frame");
        state
            .frames
            .lock()
            .unwrap()
            .push((state.started.elapsed(), frame.kind.clone()));
        match frame.kind.as_str() {
            host::PAIR => {
                let _ = out.send(reply(
                    &frame,
                    server::PAIRED,
                    json!({
                        "host_id": state.host_id,
                        "user_id": state.user_id,
                        "host_secret": HOST_SECRET,
                    }),
                ));
                let _ = out.send(Outgoing::Close(close::NORMAL, "paired"));
                return;
            }
            host::HELLO if !authorized => {
                let _ = out.send(Outgoing::Close(close::INVALID_CREDENTIAL, "bad secret"));
                return;
            }
            host::HELLO => {
                let _ = out.send(reply(
                    &frame,
                    server::WELCOME,
                    json!({
                        "host_id": state.host_id,
                        "user_id": state.user_id,
                        "protocol_version": lemma_agent_host::PROTOCOL_VERSION,
                        "heartbeat_ms": 20_000,
                    }),
                ));
                pusher = Some(tokio::spawn(push_start(state.clone(), out.clone())));
            }
            host::CONTROL => control(&state, &out, &frame),
            host::EVENTS => {
                append_events(&state, &out, &frame);
                // A poke: the host may now be owed something.
                state.poke.notify_waiters();
            }
            host::HARNESSES => {
                publish(&state, &out, &frame);
                state.poke.notify_waiters();
            }
            host::MCP | host::INTERACTION_WAIT => relay_mcp(&state, &out, frame),
            _ => {
                let _ = out.send(reply(
                    &frame,
                    server::ERROR,
                    json!({ "code": "INVALID_FRAME", "message": "unexpected", "retryable": false }),
                ));
            }
        }
    }
    if let Some(pusher) = pusher {
        pusher.abort();
    }
}

/// Push the `START_RUN` as soon as the run is dispatched and the harness is
/// published, and again until the host acknowledges it.
async fn push_start(state: BenchState, out: mpsc::UnboundedSender<Outgoing>) {
    let mut offered_at: Option<Instant> = None;
    loop {
        tokio::select! {
            () = tokio::time::sleep(START_REOFFER) => {}
            () = state.poke.notified() => {}
        }
        if out.is_closed() {
            return;
        }
        if !state.dispatched.load(Ordering::SeqCst)
            || state.start_acknowledged.load(Ordering::SeqCst)
            || offered_at.is_some_and(|offered| offered.elapsed() < START_REOFFER)
        {
            continue;
        }
        let Some(command) = start_command(&state) else {
            continue;
        };
        offered_at = Some(Instant::now());
        state.mark("Lemma pushed START_RUN");
        let _ = out.send(Outgoing::Frame(Frame {
            kind: server::COMMANDS.to_owned(),
            id: None,
            re: None,
            body: json!({ "commands": [command] }),
        }));
    }
}

fn start_command(state: &BenchState) -> Option<Command> {
    let (harness_id, revision) = state.published.lock().unwrap().clone()?;
    let command = state
        .start_command
        .lock()
        .unwrap()
        .get_or_insert_with(|| Command {
            command_id: Uuid::new_v4(),
            kind: CommandKind::StartRun,
            created_at: Utc::now(),
            expires_at: Utc::now() + chrono::Duration::minutes(4),
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
                workspace_cwd: None,
                context: std::collections::BTreeMap::new(),
                mcp: state.mcp.clone(),
                run_deadline: Utc::now() + chrono::Duration::minutes(5),
                system_prompt_delivery: None,
            })
            .unwrap(),
        })
        .clone();
    Some(command)
}

fn control(state: &BenchState, out: &mpsc::UnboundedSender<Outgoing>, frame: &Frame) {
    let offered = state
        .start_command
        .lock()
        .unwrap()
        .as_ref()
        .map(|command| command.command_id.to_string());
    if let Some(offered) = offered
        && frame.body["acknowledged_command_ids"]
            .as_array()
            .is_some_and(|ids| ids.iter().any(|id| id.as_str() == Some(offered.as_str())))
        && !state.start_acknowledged.swap(true, Ordering::SeqCst)
    {
        state.mark("host acknowledged START_RUN");
    }
    // Commands travel by push; the answer carries none, so a heartbeat is
    // never how work arrives.
    let _ = out.send(reply(
        frame,
        server::CONTROL_OK,
        json!({ "commands": [], "refused": [] }),
    ));
}

fn publish(state: &BenchState, out: &mpsc::UnboundedSender<Outgoing>, frame: &Frame) {
    let snapshots = frame.body["harnesses"]
        .as_array()
        .cloned()
        .unwrap_or_default();
    let items = snapshots
        .iter()
        .map(|snapshot| {
            let key = snapshot["harness_key"]
                .as_str()
                .unwrap_or_default()
                .to_owned();
            let id = *state
                .harness_ids
                .lock()
                .unwrap()
                .entry(key.clone())
                .or_insert_with(Uuid::new_v4);
            if key == state.harness_key && snapshot["health"].as_str() == Some("READY") {
                let mut published = state.published.lock().unwrap();
                if published.is_none() {
                    state.mark("harness published READY");
                }
                *published = Some((
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
    let _ = out.send(reply(
        frame,
        server::HARNESSES_OK,
        json!({ "items": items }),
    ));
}

fn append_events(state: &BenchState, out: &mpsc::UnboundedSender<Outgoing>, frame: &Frame) {
    let batch: EventBatch = serde_json::from_value(frame.body.clone()).unwrap();
    let Some(last) = batch.events.last() else {
        return;
    };
    let ack = json!({ "ack": {
        "run_id": last.run_id,
        "lease_epoch": last.lease_epoch,
        "acked_through": last.sequence,
    }});
    {
        let mut events = state.events.lock().unwrap();
        let had_text = events
            .iter()
            .any(|event| event.event_type == EventType::AgentMessageChunk);
        let brings_text = batch
            .events
            .iter()
            .any(|event| event.event_type == EventType::AgentMessageChunk);
        if !had_text && brings_text {
            state.mark("first assistant text reached Lemma");
        }
        if batch
            .events
            .iter()
            .any(|event| event.event_type == EventType::Terminal)
        {
            state.mark("terminal reached Lemma");
        }
        state.mark(format!("events batch of {}", batch.events.len()));
        events.extend(batch.events);
    }
    let _ = out.send(reply(frame, server::EVENTS_OK, ack));
}

/// The agent's calls to Lemma's tools, answered by the tests' MCP stand-in.
fn relay_mcp(state: &BenchState, out: &mpsc::UnboundedSender<Outgoing>, frame: Frame) {
    let Some(endpoint) = state.mcp_endpoint.clone() else {
        let _ = out.send(reply(
            &frame,
            server::ERROR,
            json!({ "code": "NOT_FOUND", "message": "no Lemma tools", "retryable": false }),
        ));
        return;
    };
    let out = out.clone();
    // An interaction wait is held until the person decides; it must not hold
    // up the frames behind it.
    tokio::spawn(async move {
        let answered = if frame.kind == host::MCP {
            endpoint
                .answer(&frame.body)
                .map(|result| (server::MCP_OK, json!({ "result": result })))
        } else {
            endpoint
                .wait_for_decision(&frame.body)
                .await
                .map(|answer| (server::INTERACTION_OK, json!({ "answer": answer })))
        };
        let answer = match answered {
            Ok((kind, body)) => reply(&frame, kind, body),
            Err(support::McpFailure::Refused {
                code,
                message,
                retryable,
            }) => reply(
                &frame,
                server::ERROR,
                json!({ "code": code, "message": message, "retryable": retryable }),
            ),
            Err(support::McpFailure::DropLink) => {
                Outgoing::Close(close::RESTARTING, "the stand-in dropped the link")
            }
        };
        let _ = out.send(answer);
    });
}

struct Bench {
    state: BenchState,
    base_url: url::Url,
}

impl Bench {
    async fn start(state: BenchState) -> Self {
        let app = Router::new()
            .route("/agent-host/link", get(upgrade))
            .with_state(state.clone());
        let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
        let address = listener.local_addr().unwrap();
        tokio::spawn(async move {
            let _ = axum::serve(listener, app).await;
        });
        Self {
            state,
            base_url: url::Url::parse(&format!("http://{address}/")).unwrap(),
        }
    }

    fn saw_terminal(&self) -> bool {
        self.state
            .events
            .lock()
            .unwrap()
            .iter()
            .any(|event| event.event_type == EventType::Terminal)
    }

    async fn wait_until(&self, what: &str, budget: Duration, predicate: impl Fn(&Self) -> bool) {
        let deadline = Instant::now() + budget;
        while Instant::now() < deadline {
            if predicate(self) {
                return;
            }
            tokio::time::sleep(Duration::from_millis(5)).await;
        }
        panic!(
            "timed out waiting for {what}; hops={:?}",
            self.state.hops.lock().unwrap()
        );
    }
}

// ---------------------------------------------------------------------------
// `turn`: one real agent turn, hop by hop.
// ---------------------------------------------------------------------------

#[cfg_attr(
    windows,
    expect(unused_variables, reason = "adapter reuse is a unix symlink")
)]
async fn start_host(
    root: &std::path::Path,
    bench: &Bench,
    adapters: &std::path::Path,
) -> tokio::task::JoinHandle<anyhow::Result<()>> {
    let paths = HostPaths::under(root);
    paths.ensure().unwrap();
    #[cfg(unix)]
    {
        let _ = std::fs::remove_dir_all(&paths.adapters);
        std::os::unix::fs::symlink(adapters, &paths.adapters).unwrap();
    }
    let installation_id = Uuid::new_v4().to_string();
    let target = lemma_agent_host::link::pair(
        bench.base_url.clone(),
        "latency-bench-pairing-code",
        "Bench host",
        &installation_id,
        true,
    )
    .await
    .unwrap();
    let config = lemma_agent_host::config::HostConfig {
        legacy_host_execution: false,
        installation_id,
        targets: vec![target],
        max_runs: 1,
    };
    config.save(&paths).unwrap();
    let runtime = lemma_agent_host::runtime::HostRuntime::new(config, paths)
        .unwrap()
        .with_mcp_bridge_executable(PathBuf::from(env!("CARGO_BIN_EXE_lemma-agent-host")));
    tokio::spawn(runtime.serve())
}

async fn measure_turn(harness: &str, prompt: &str) {
    let source = HostPaths::under(
        std::env::var_os("LEMMA_REAL_AGENT_HOST_DATA_DIR")
            .map(PathBuf::from)
            .expect("set LEMMA_REAL_AGENT_HOST_DATA_DIR"),
    );
    let directory = TempDir::new().unwrap();
    // A run-scoped MCP configuration, because the host refuses a START_RUN
    // without one — and because standing the bridge up is part of what a turn
    // pays for.
    let endpoint = support::LemmaMcpEndpoint::new();
    let bench = Bench::start(BenchState::new(harness, prompt, Some(endpoint))).await;
    let host = start_host(directory.path(), &bench, &source.adapters).await;

    // Wait for the host to be idle and linked: the harness published and the
    // link settled. This is what the desktop app looks like when the user is
    // sitting in front of a conversation about to type.
    bench
        .wait_until("the harness to publish", Duration::from_secs(90), |bench| {
            bench.state.published.lock().unwrap().is_some()
        })
        .await;
    tokio::time::sleep(Duration::from_secs(3)).await;

    // "The user pressed enter." Lemma pokes the link the moment it dispatches.
    bench.state.mark("user sent the message (run dispatched)");
    bench.state.dispatched.store(true, Ordering::SeqCst);
    bench.state.poke.notify_waiters();

    bench
        .wait_until(
            "the run to finish",
            Duration::from_secs(240),
            Bench::saw_terminal,
        )
        .await;
    host.abort();
    report_turn(&bench, harness);
}

fn report_turn(bench: &Bench, harness: &str) {
    let hops = bench.state.hops.lock().unwrap().clone();
    let dispatch_at = hops
        .iter()
        .find(|hop| hop.what.starts_with("user sent"))
        .map(|hop| hop.at)
        .unwrap();
    println!("\n=== mode=turn harness={harness} ===");
    for hop in &hops {
        if hop.at < dispatch_at && !hop.what.starts_with("harness") {
            continue;
        }
        match hop.at.checked_sub(dispatch_at) {
            Some(delta) => println!("  +{:7.3}s  {}", delta.as_secs_f64(), hop.what),
            None => println!(
                "  ({:7.3}s before dispatch)  {}",
                dispatch_at.saturating_sub(hop.at).as_secs_f64(),
                hop.what
            ),
        }
    }
    let appends = hops
        .iter()
        .filter(|hop| hop.what.starts_with("events batch"))
        .map(|hop| hop.at)
        .collect::<Vec<_>>();
    let gaps = appends
        .windows(2)
        .map(|pair| pair[1].saturating_sub(pair[0]))
        .collect::<Vec<_>>();
    if let Some(summary) = summarize(gaps) {
        println!("  batch gaps: {summary}");
    }
    println!(
        "  events delivered: {}",
        bench.state.events.lock().unwrap().len()
    );
    // What the turn cost Lemma. Over the poll it was a poll per wake and a
    // POST per batch; over the link it is one frame per request, on one
    // socket that authenticated once.
    let mut after_dispatch: Vec<(String, usize)> = Vec::new();
    for (at, kind) in bench.state.frames.lock().unwrap().iter() {
        if *at < dispatch_at {
            continue;
        }
        match after_dispatch.iter_mut().find(|(seen, _)| seen == kind) {
            Some((_, count)) => *count += 1,
            None => after_dispatch.push((kind.clone(), 1)),
        }
    }
    let frames = after_dispatch
        .iter()
        .map(|(kind, count)| format!("{kind}={count}"))
        .collect::<Vec<_>>()
        .join(" ");
    println!("  host frames after dispatch: {frames}");
    let since_dispatch = |prefix: &str| {
        hops.iter()
            .find(|hop| hop.what.starts_with(prefix))
            .map(|hop| hop.at.saturating_sub(dispatch_at))
    };
    println!("  ---");
    println!(
        "  time to START_RUN acknowledged:         {:?}",
        since_dispatch("host acknowledged")
    );
    println!(
        "  time to first assistant text at Lemma: {:?}",
        since_dispatch("first assistant text")
    );
    println!(
        "  time to terminal at Lemma:             {:?}",
        since_dispatch("terminal")
    );
    println!("  event batches delivered:               {}", appends.len());
}

// ---------------------------------------------------------------------------
// `link`: what one request costs on the link, with no agent in the way.
// ---------------------------------------------------------------------------

async fn measure_link() {
    let bench = Bench::start(BenchState::new("none", "", None)).await;
    let connected = lemma_agent_host::link::connect(
        &bench.base_url,
        HOST_SECRET,
        HostHello::current(Uuid::new_v4().to_string()),
        HostCapacity {
            max_runs: 1,
            active_runs: 0,
            available_runs: 1,
        },
    )
    .await
    .expect("the stand-in welcomes the bench's hello");
    let link = connected.handle;

    let control = ControlBody::default();
    let mut controls = Vec::with_capacity(LINK_ROUND_TRIPS);
    for _ in 0..LINK_ROUND_TRIPS {
        let sent = Instant::now();
        link.control(&control).await.expect("control answered");
        controls.push(sent.elapsed());
    }

    // One streamed chunk per batch: the shape a live answer takes when each
    // token is delivered as soon as it exists.
    let run_id = Uuid::new_v4();
    let mut appends = Vec::with_capacity(LINK_ROUND_TRIPS);
    for sequence in 1..=LINK_ROUND_TRIPS as u64 {
        let mut payload = JsonMap::new();
        payload.insert("text".to_owned(), json!("token "));
        let batch = EventBatch {
            events: vec![Event {
                run_id,
                lease_epoch: 1,
                sequence,
                event_type: EventType::AgentMessageChunk,
                object_id: Some("message".to_owned()),
                payload,
            }],
        };
        let sent = Instant::now();
        link.append_events(&batch).await.expect("events answered");
        appends.push(sent.elapsed());
    }
    link.close(close::NORMAL, "measured");

    println!("\n=== mode=link ({LINK_ROUND_TRIPS} round trips each) ===");
    if let Some(summary) = summarize(controls) {
        println!("  control round trip: {summary}");
    }
    if let Some(summary) = summarize(appends) {
        println!("  events round trip:  {summary}");
    }
}

/// Median, p90 and max of a set of durations, in milliseconds.
fn summarize(mut samples: Vec<Duration>) -> Option<String> {
    if samples.is_empty() {
        return None;
    }
    samples.sort();
    let at = |fraction: usize| samples[(samples.len() * fraction / 100).min(samples.len() - 1)];
    let ms = |duration: Duration| duration.as_secs_f64() * 1_000.0;
    Some(format!(
        "median {:.3}ms  p90 {:.3}ms  max {:.3}ms  (n={})",
        ms(at(50)),
        ms(at(90)),
        ms(samples[samples.len() - 1]),
        samples.len(),
    ))
}

/// What to measure, from the command line.
///
/// `cargo bench -p lemma-agent-host -- turn` measures one real agent turn;
/// `link` the link's own round trips. Both, with no argument.
fn main() {
    let requested: Vec<String> = std::env::args().skip(1).collect();
    let modes: Vec<Mode> = if requested.iter().any(|arg| arg == "turn") {
        vec![Mode::Turn]
    } else if requested.iter().any(|arg| arg == "link") {
        vec![Mode::Link]
    } else {
        vec![Mode::Link, Mode::Turn]
    };
    let harness = std::env::var("LEMMA_BENCH_HARNESS").unwrap_or_else(|_| "claude-code".to_owned());
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .expect("a runtime to measure on");
    for mode in modes {
        match mode {
            Mode::Turn => runtime.block_on(measure_turn(&harness, &bench_prompt())),
            Mode::Link => runtime.block_on(measure_link()),
        }
    }
}

/// The turn to measure. Short by default, so the number is startup overhead;
/// `LEMMA_BENCH_PROMPT` swaps in a long one to measure streaming cadence.
fn bench_prompt() -> String {
    std::env::var("LEMMA_BENCH_PROMPT").unwrap_or_else(|_| "Reply with exactly: PONG".to_owned())
}
