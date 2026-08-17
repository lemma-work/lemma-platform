//! Where a turn's wall clock goes between Lemma and a real local agent.
//!
//! Not a correctness test — a measurement. It stands a control plane in front
//! of the real `HostRuntime` and timestamps every hop, in two modes:
//!
//! * `fast` — the poll answers immediately (what the hermetic tests use).
//! * `real` — the poll behaves like `agent_host_controller.poll`: it holds for
//!   `POLL_HOLD` when idle, wakes on a poke, and answers a poll carrying
//!   control updates with `poll_after_ms=1000`.
//!
//! It measures the host's own hops only. "The user sent a message" is a
//! dispatch simulated inside this test, so nothing here covers the backend
//! pipeline a real message travels first.
//!
//! ```text
//! LEMMA_REAL_AGENT_HOST_DATA_DIR=... cargo test --test latency_bench -- --ignored --nocapture
//! ```

use std::path::PathBuf;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{post, put};
use axum::{Json, Router};
use chrono::Utc;
use lemma_agent_host::config::HostPaths;
use lemma_agent_host::protocol::{Event, EventBatch, EventType, JsonMap, RunSpec};
use serde_json::{Value, json};
use tempfile::TempDir;
use tokio::net::TcpListener;
use uuid::Uuid;

mod support;

const HOST_SECRET: &str = "latency-bench-host-secret-with-entropy";
/// What the real backend answers a poll that carried control updates.
const CONTROL_UPDATE_BACKOFF_MS: u64 = 1_000;
const POLL_HOLD: Duration = Duration::from_secs(25);

#[derive(Clone, Copy, PartialEq, Eq)]
enum Mode {
    Fast,
    Real,
}

/// One hop, stamped when it happened.
#[derive(Clone, Debug)]
struct Hop {
    at: Duration,
    what: String,
}

#[derive(Clone)]
struct BenchState {
    mode: Mode,
    started: Instant,
    host_id: Uuid,
    user_id: Uuid,
    run_id: Uuid,
    harness_key: String,
    prompt: String,
    mcp: Value,
    published: Arc<Mutex<Option<(Uuid, String)>>>,
    /// Set by the test when it "sends the message", i.e. dispatches the run.
    dispatch_at: Arc<Mutex<Option<Instant>>>,
    start_sent: Arc<AtomicBool>,
    hops: Arc<Mutex<Vec<Hop>>>,
    events: Arc<Mutex<Vec<Event>>>,
    polls: Arc<Mutex<Vec<(Duration, Duration)>>>,
    /// Last state each run checkpointed, so a repeated heartbeat is not news.
    checkpoint_states: Arc<Mutex<std::collections::HashMap<String, String>>>,
}

impl BenchState {
    fn mark(&self, what: impl Into<String>) {
        self.hops.lock().unwrap().push(Hop {
            at: self.started.elapsed(),
            what: what.into(),
        });
    }
}

fn require_auth(headers: &HeaderMap) -> Result<(), StatusCode> {
    let value = headers
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        .unwrap_or_default();
    (value == format!("Bearer {HOST_SECRET}"))
        .then_some(())
        .ok_or(StatusCode::UNAUTHORIZED)
}

async fn pairing(State(state): State<BenchState>, Json(_body): Json<Value>) -> Json<Value> {
    Json(json!({
        "host_id": state.host_id,
        "user_id": state.user_id,
        "organization_id": null,
        "host_secret": HOST_SECRET,
    }))
}

async fn publish(
    State(state): State<BenchState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, StatusCode> {
    require_auth(&headers)?;
    let snapshots = body["harnesses"].as_array().cloned().unwrap_or_default();
    let items = snapshots
        .iter()
        .map(|snapshot| {
            let id = Uuid::new_v4();
            if snapshot["harness_key"].as_str() == Some(state.harness_key.as_str())
                && snapshot["health"].as_str() == Some("READY")
            {
                let mut published = state.published.lock().unwrap();
                if published.is_none() {
                    state.mark("harness published READY");
                }
                *published = Some((
                    id,
                    snapshot["config_revision"].as_str().unwrap_or("").to_owned(),
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
    Ok(Json(json!({"items": items})))
}

fn start_command(state: &BenchState, harness_id: Uuid, revision: String) -> Value {
    let payload = serde_json::to_value(RunSpec {
        agent_run_id: state.run_id,
        conversation_id: Uuid::new_v4(),
        harness_id,
        profile_revision: revision,
        model_name: None,
        config_selections: JsonMap::new(),
        system_prompt: "Follow the runtime instructions exactly.".to_owned(),
        prompt: vec![json!({"type": "text", "text": state.prompt})],
        resume_session_id: None,
        context: std::collections::BTreeMap::new(),
        mcp: state.mcp.clone(),
        run_deadline: Utc::now() + chrono::Duration::minutes(5),
        system_prompt_delivery: None,
    })
    .unwrap();
    json!({
        "command_id": Uuid::new_v4(),
        "kind": "START_RUN",
        "created_at": Utc::now(),
        "expires_at": Utc::now() + chrono::Duration::minutes(4),
        "run_id": state.run_id,
        "lease_epoch": 1,
        "payload": payload,
    })
}

/// Whether the run has been dispatched and the `START_RUN` is still owed.
fn owed_start(state: &BenchState) -> Option<Value> {
    let dispatched = state.dispatch_at.lock().unwrap().is_some();
    if !dispatched {
        return None;
    }
    let published = state.published.lock().unwrap().clone();
    let (harness_id, revision) = published?;
    if state.start_sent.swap(true, Ordering::SeqCst) {
        return None;
    }
    state.mark("control plane handed START_RUN to the poll");
    Some(start_command(state, harness_id, revision))
}

async fn poll(
    State(state): State<BenchState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, StatusCode> {
    require_auth(&headers)?;
    let opened = state.started.elapsed();
    // What `agent_host_dispatch_repository.poll_commands` calls `progressed`:
    // an acknowledgement, or a checkpoint that *advanced* a run's state. A
    // repeated heartbeat for a run already in that state is not news, which is
    // the whole reason a host with work in flight is still allowed to long-poll.
    let acknowledged = body["acknowledged_command_ids"]
        .as_array()
        .is_some_and(|items| !items.is_empty());
    let mut advanced = false;
    if let Some(checkpoints) = body["checkpoints"].as_array() {
        let mut seen = state.checkpoint_states.lock().unwrap();
        for checkpoint in checkpoints {
            let run = checkpoint["run_id"].as_str().unwrap_or_default().to_owned();
            let reported = checkpoint["state"].as_str().unwrap_or_default().to_owned();
            if seen.insert(run, reported.clone()).as_deref() != Some(reported.as_str()) {
                advanced = true;
            }
        }
    }
    let progressed = acknowledged || advanced;

    if let Some(command) = owed_start(&state) {
        state.polls.lock().unwrap().push((opened, state.started.elapsed()));
        return Ok(Json(json!({
            "protocol_version": 2,
            "host_status": "ONLINE",
            "commands": [command],
            "poll_after_ms": 0,
        })));
    }

    if state.mode == Mode::Real && progressed {
        state.polls.lock().unwrap().push((opened, state.started.elapsed()));
        return Ok(Json(json!({
            "protocol_version": 2,
            "host_status": "ONLINE",
            "commands": [],
            "poll_after_ms": CONTROL_UPDATE_BACKOFF_MS,
        })));
    }

    if state.mode == Mode::Real {
        // Idle: hold like the real backend, waking early only for a dispatch.
        let deadline = Instant::now() + POLL_HOLD;
        loop {
            if let Some(command) = owed_start(&state) {
                state.polls.lock().unwrap().push((opened, state.started.elapsed()));
                return Ok(Json(json!({
                    "protocol_version": 2,
                    "host_status": "ONLINE",
                    "commands": [command],
                    "poll_after_ms": 0,
                })));
            }
            if Instant::now() >= deadline {
                break;
            }
            tokio::time::sleep(Duration::from_millis(10)).await;
        }
    }

    state.polls.lock().unwrap().push((opened, state.started.elapsed()));
    Ok(Json(json!({
        "protocol_version": 2,
        "host_status": "ONLINE",
        "commands": [],
        "poll_after_ms": if state.mode == Mode::Fast { 25 } else { 0 },
    })))
}

async fn append_events(
    State(state): State<BenchState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, StatusCode> {
    require_auth(&headers)?;
    let batch: EventBatch = serde_json::from_value(body).unwrap();
    let last = batch.events.last().unwrap();
    let response = json!({
        "run_id": last.run_id,
        "lease_epoch": last.lease_epoch,
        "acked_through": last.sequence,
    });
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
        state.mark(format!("events:append batch of {}", batch.events.len()));
        events.extend(batch.events);
    }
    Ok(Json(response))
}

struct Bench {
    state: BenchState,
    base_url: url::Url,
}

impl Bench {
    async fn start(mode: Mode, harness: &str, prompt: &str, mcp: Value) -> Self {
        let state = BenchState {
            mcp,
            mode,
            started: Instant::now(),
            host_id: Uuid::new_v4(),
            user_id: Uuid::new_v4(),
            run_id: Uuid::new_v4(),
            harness_key: harness.to_owned(),
            prompt: prompt.to_owned(),
            published: Arc::new(Mutex::new(None)),
            dispatch_at: Arc::new(Mutex::new(None)),
            start_sent: Arc::new(AtomicBool::new(false)),
            hops: Arc::new(Mutex::new(Vec::new())),
            events: Arc::new(Mutex::new(Vec::new())),
            polls: Arc::new(Mutex::new(Vec::new())),
            checkpoint_states: Arc::new(Mutex::new(std::collections::HashMap::new())),
        };
        let app = Router::new()
            .route("/agent-host/pairings:complete", post(pairing))
            .route("/agent-host/harnesses", put(publish))
            .route("/agent-host/poll", post(poll))
            .route("/agent-host/events:append", post(append_events))
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
        panic!("timed out waiting for {what}; hops={:?}", self.state.hops.lock().unwrap());
    }
}

async fn start_host(root: &std::path::Path, bench: &Bench, adapters: &std::path::Path) -> tokio::task::JoinHandle<anyhow::Result<()>> {
    let paths = HostPaths::under(root);
    paths.ensure().unwrap();
    #[cfg(unix)]
    {
        let _ = std::fs::remove_dir_all(&paths.adapters);
        std::os::unix::fs::symlink(adapters, &paths.adapters).unwrap();
    }
    let installation_id = Uuid::new_v4().to_string();
    let target = lemma_agent_host::api::TargetClient::pair(
        bench.base_url.clone(),
        "latency-bench-pairing-code",
        "Bench host",
        &installation_id,
        true,
    )
    .await
    .unwrap();
    let config = lemma_agent_host::config::HostConfig {
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

async fn measure(mode: Mode, harness: &str, prompt: &str) {
    let source = HostPaths::under(
        std::env::var_os("LEMMA_REAL_AGENT_HOST_DATA_DIR")
            .map(PathBuf::from)
            .expect("set LEMMA_REAL_AGENT_HOST_DATA_DIR"),
    );
    let directory = TempDir::new().unwrap();
    // A run-scoped MCP endpoint, because the host refuses a START_RUN without
    // one — and because standing the bridge up is part of what a turn pays for.
    let endpoint = support::LemmaMcpEndpoint::start(support::McpTransport::StatelessJson).await;
    let bench = Bench::start(mode, harness, prompt, endpoint.run_configuration()).await;
    let host = start_host(directory.path(), &bench, &source.adapters).await;

    // Wait for the host to be idle and paired: the harness published and the
    // poll loop settled. This is what the desktop app looks like when the user
    // is sitting in front of a conversation about to type.
    bench
        .wait_until("the harness to publish", Duration::from_secs(90), |bench| {
            bench.state.published.lock().unwrap().is_some()
        })
        .await;
    // Let the loop enter a held poll, so the dispatch below has to interrupt a
    // real idle wait rather than landing on a loop that happens to be spinning.
    tokio::time::sleep(Duration::from_secs(3)).await;

    // "The user pressed enter."
    let dispatch = Instant::now();
    *bench.state.dispatch_at.lock().unwrap() = Some(dispatch);
    bench.state.mark("user sent the message (run dispatched)");

    bench
        .wait_until("the run to finish", Duration::from_secs(240), Bench::saw_terminal)
        .await;
    host.abort();

    let hops = bench.state.hops.lock().unwrap().clone();
    let dispatch_at = hops
        .iter()
        .find(|hop| hop.what.starts_with("user sent"))
        .map(|hop| hop.at)
        .unwrap();
    println!("\n=== mode={} harness={harness} ===", match mode {
        Mode::Fast => "fast",
        Mode::Real => "real",
    });
    for hop in &hops {
        if hop.at < dispatch_at && !hop.what.starts_with("harness") {
            continue;
        }
        let relative = hop.at.checked_sub(dispatch_at);
        match relative {
            Some(delta) => println!("  +{:7.3}s  {}", delta.as_secs_f64(), hop.what),
            None => println!("  ({:7.3}s before dispatch)  {}", dispatch_at.saturating_sub(hop.at).as_secs_f64(), hop.what),
        }
    }
    let appends = hops
        .iter()
        .filter(|hop| hop.what.starts_with("events:append"))
        .map(|hop| hop.at)
        .collect::<Vec<_>>();
    let batches = appends.len();
    let mut gaps = appends
        .windows(2)
        .map(|pair| pair[1].saturating_sub(pair[0]).as_secs_f64())
        .collect::<Vec<_>>();
    gaps.sort_by(f64::total_cmp);
    if !gaps.is_empty() {
        println!(
            "  batch gaps: median {:.3}s  p90 {:.3}s  max {:.3}s  (n={})",
            gaps[gaps.len() / 2],
            gaps[(gaps.len() * 9 / 10).min(gaps.len() - 1)],
            gaps[gaps.len() - 1],
            gaps.len(),
        );
    }
    println!(
        "  events delivered: {}",
        bench.state.events.lock().unwrap().len()
    );
    // The number that mattered. A wake of the poll loop abandons the poll in
    // flight, and the server goes on holding the abandoned one for the rest of
    // its hold — so a host that re-polls per streamed event stacks them. Peak
    // concurrency is what the control plane actually pays; on a real backend a
    // single streamed answer was measured reaching 26, against one when idle.
    let polls = bench.state.polls.lock().unwrap();
    let during = polls
        .iter()
        .filter(|(opened, _)| *opened >= dispatch_at)
        .count();
    let mut edges = Vec::new();
    for (opened, closed) in polls.iter() {
        edges.push((*opened, 1_i32));
        edges.push((*closed, -1));
    }
    edges.sort();
    let (mut open_now, mut peak) = (0, 0);
    for (_, delta) in edges {
        open_now += delta;
        peak = peak.max(open_now);
    }
    println!(
        "  polls opened: {} total, {during} after dispatch; peak concurrent: {peak}",
        polls.len()
    );
    let first_text = hops
        .iter()
        .find(|hop| hop.what.starts_with("first assistant text"))
        .map(|hop| hop.at.saturating_sub(dispatch_at));
    let terminal = hops
        .iter()
        .find(|hop| hop.what.starts_with("terminal"))
        .map(|hop| hop.at.saturating_sub(dispatch_at));
    println!("  ---");
    println!("  time to first assistant text at Lemma: {first_text:?}");
    println!("  time to terminal at Lemma:             {terminal:?}");
    println!("  event batches delivered:               {batches}");
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "measurement; spends real provider quota"]
async fn turn_latency_with_a_fast_control_plane() {
    let harness = std::env::var("LEMMA_BENCH_HARNESS").unwrap_or_else(|_| "claude-code".to_owned());
    measure(Mode::Fast, &harness, &bench_prompt()).await;
}

#[tokio::test(flavor = "multi_thread")]
#[ignore = "measurement; spends real provider quota"]
async fn turn_latency_with_a_realistic_control_plane() {
    let harness = std::env::var("LEMMA_BENCH_HARNESS").unwrap_or_else(|_| "claude-code".to_owned());
    measure(Mode::Real, &harness, &bench_prompt()).await;
}

/// The turn to measure. Short by default, so the number is startup overhead;
/// `LEMMA_BENCH_PROMPT` swaps in a long one to measure streaming cadence.
fn bench_prompt() -> String {
    std::env::var("LEMMA_BENCH_PROMPT")
        .unwrap_or_else(|_| "Reply with exactly: PONG".to_owned())
}
