//! Opt-in tests against the developer's authenticated local ACP harnesses.
//!
//! These are ignored in CI because they spend real provider quota and depend on
//! local Codex, Claude Code, and `OpenCode` credentials.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use axum::extract::State;
use axum::http::{HeaderMap, StatusCode};
use axum::routing::{post, put};
use axum::{Json, Router};
use chrono::Utc;

use lemma_agent_host::acp::{AcpCallbacks, AcpDriver, AcpRunRequest, AgentDriver};
use lemma_agent_host::adapters::AdapterManifest;
use lemma_agent_host::api::TargetClient;
use lemma_agent_host::config::{HostConfig, HostPaths};
use lemma_agent_host::permissions::PermissionGate;
use lemma_agent_host::protocol::{Event, EventBatch, EventType, JsonMap, RunSpec, RunState};
use lemma_agent_host::runtime::HostRuntime;
use serde_json::{Value, json};
use tempfile::TempDir;
use tokio::net::TcpListener;
use uuid::Uuid;

const HOST_SECRET: &str = "real-control-e2e-host-secret";

#[derive(Default)]
struct StreamCapture {
    session_started: AtomicBool,
    events: Mutex<Vec<(EventType, JsonMap)>>,
}

impl AcpCallbacks for StreamCapture {
    fn before_prompt(&self, provider_session_id: &str) -> anyhow::Result<()> {
        anyhow::ensure!(
            !provider_session_id.is_empty(),
            "ACP returned an empty session id"
        );
        self.session_started.store(true, Ordering::SeqCst);
        Ok(())
    }

    fn event(
        &self,
        event_type: EventType,
        _object_id: Option<String>,
        payload: JsonMap,
    ) -> anyhow::Result<()> {
        anyhow::ensure!(
            self.session_started.load(Ordering::SeqCst),
            "streamed an event before the provider accepted the session"
        );
        self.events.lock().unwrap().push((event_type, payload));
        Ok(())
    }
}

fn configured_agents() -> Vec<String> {
    std::env::var("LEMMA_REAL_AGENT_E2E_AGENTS")
        .unwrap_or_else(|_| "codex,claude-code,opencode".to_owned())
        .split(',')
        .map(str::trim)
        .filter(|agent| !agent.is_empty())
        .map(str::to_owned)
        .collect()
}

fn agent_host_data_directory() -> PathBuf {
    std::env::var_os("LEMMA_REAL_AGENT_HOST_DATA_DIR")
        .or_else(|| std::env::var_os("LEMMA_AGENT_HOST_DATA_DIR"))
        .map(PathBuf::from)
        .expect(
            "set LEMMA_REAL_AGENT_HOST_DATA_DIR to an Agent Host directory whose pinned \
             adapters have been installed with `lemma agent-host doctor --repair`",
        )
}

#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn authenticated_harnesses_stream_real_answers_over_acp() {
    let paths = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(paths.adapters.clone());

    if std::env::var_os("LEMMA_REAL_AGENT_E2E_SKIP_DIRECT").is_none() {
        for agent in configured_agents() {
            let marker = format!("LEMMA_{}_STREAM_OK", agent.replace('-', "_").to_uppercase());
            let callbacks = std::sync::Arc::new(StreamCapture::default());
            let run_id = Uuid::new_v4();
            let outcome = AcpDriver
                .run(
                    AcpRunRequest {
                        adapter: manifest.resolve(&agent).unwrap(),
                        run_spec: RunSpec {
                            agent_run_id: run_id,
                            conversation_id: Uuid::new_v4(),
                            harness_id: Uuid::new_v4(),
                            profile_revision: "real-harness-e2e".to_owned(),
                            model_name: None,
                            config_selections: JsonMap::new(),
                            system_prompt: "Follow the user's output format exactly.".to_owned(),
                            prompt: vec![serde_json::json!({
                                "type": "text",
                                "text": format!("Reply with exactly: {marker}"),
                            })],
                            context: BTreeMap::new(),
                            mcp: Value::Null,
                            run_deadline: chrono::Utc::now() + chrono::Duration::minutes(5),
                        },
                        scratch_directory: paths
                            .root
                            .join("real-e2e")
                            .join(agent.as_str())
                            .join(run_id.to_string()),
                        mcp_server: None,
                        permissions: PermissionGate::new(),
                        permission_timeout: Duration::ZERO,
                    },
                    callbacks.clone(),
                )
                .await
                .unwrap_or_else(|error| panic!("{agent} ACP run failed: {error:#}"));

            assert_eq!(
                outcome.state,
                RunState::Succeeded,
                "{agent} did not succeed"
            );
            assert!(
                callbacks.session_started.load(Ordering::SeqCst),
                "{agent} never accepted an ACP session"
            );

            let events = callbacks.events.lock().unwrap();
            let answer = events
                .iter()
                .filter(|(event_type, _)| *event_type == EventType::AgentMessageChunk)
                .filter_map(|(_, payload)| payload.get("text").and_then(Value::as_str))
                .collect::<String>();
            assert!(
                answer.contains(&marker),
                "{agent} did not stream the marker in assistant-message chunks; answer={answer:?}"
            );
            assert!(
                events
                    .iter()
                    .any(|(event_type, _)| *event_type == EventType::AgentMessageChunk),
                "{agent} returned no streamed assistant-message event"
            );
            println!("{agent}: {marker} ({})", outcome.provider_session_id);
        }
    }

    run_through_paired_agent_host(&paths, "codex").await;
}

#[tokio::test]
#[ignore = "requires authenticated Codex and spends real image-generation quota"]
async fn codex_native_image_generation_creates_a_publishable_artifact() {
    if std::env::var("LEMMA_REAL_AGENT_E2E_IMAGE").as_deref() != Ok("1") {
        eprintln!("set LEMMA_REAL_AGENT_E2E_IMAGE=1 to run the native image test");
        return;
    }
    let paths = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(paths.adapters.clone());
    let scratch = TempDir::new().unwrap();
    let callbacks = Arc::new(StreamCapture::default());
    let run = AcpDriver.run(
        AcpRunRequest {
            adapter: manifest.resolve("codex").unwrap(),
            run_spec: RunSpec {
                agent_run_id: Uuid::new_v4(),
                conversation_id: Uuid::new_v4(),
                harness_id: Uuid::new_v4(),
                profile_revision: "real-image-e2e".to_owned(),
                model_name: None,
                config_selections: JsonMap::new(),
                system_prompt: concat!(
                    "Use Codex's built-in $imagegen capability for image requests. ",
                    "Do not use Pillow, SVG, canvas, Python, shell scripts, or an ",
                    "external image CLI. Copy final images into .lemma-artifacts."
                )
                .to_owned(),
                prompt: vec![json!({
                    "type": "text",
                    "text": concat!(
                        "Create a simple square diagnostic poster with a navy ",
                        "background and the exact white text LEMMA IMAGE OK. Save ",
                        "the final PNG as .lemma-artifacts/native-image-e2e.png."
                    ),
                })],
                context: JsonMap::new(),
                mcp: Value::Null,
                run_deadline: Utc::now() + chrono::Duration::minutes(10),
            },
            scratch_directory: scratch.path().to_path_buf(),
            mcp_server: None,
            permissions: PermissionGate::new(),
            permission_timeout: Duration::ZERO,
        },
        callbacks,
    );
    let outcome = tokio::time::timeout(Duration::from_secs(600), run)
        .await
        .expect("Codex image generation timed out")
        .expect("Codex image generation failed");
    assert_eq!(outcome.state, RunState::Succeeded);

    let image = scratch
        .path()
        .join(".lemma-artifacts")
        .join("native-image-e2e.png");
    let bytes = std::fs::read(&image)
        .unwrap_or_else(|error| panic!("Codex did not create {}: {error}", image.display()));
    assert!(
        bytes.starts_with(b"\x89PNG\r\n\x1a\n"),
        "Codex image artifact is not a PNG"
    );
    println!("codex native image: {} bytes", bytes.len());
}

#[derive(Clone)]
struct ControlServerState {
    host_id: Uuid,
    user_id: Uuid,
    run_id: Uuid,
    selected_agent: String,
    published: Arc<Mutex<Option<(Uuid, String)>>>,
    command_sent: Arc<AtomicBool>,
    events: Arc<Mutex<Vec<Event>>>,
}

async fn pairing(State(state): State<ControlServerState>, Json(_body): Json<Value>) -> Json<Value> {
    Json(json!({
        "host_id": state.host_id,
        "user_id": state.user_id,
        "organization_id": null,
        "host_secret": HOST_SECRET,
    }))
}

async fn publish(
    State(state): State<ControlServerState>,
    headers: HeaderMap,
    Json(body): Json<Value>,
) -> Result<Json<Value>, StatusCode> {
    require_auth(&headers)?;
    let snapshots = body["harnesses"].as_array().unwrap();
    let items = snapshots
        .iter()
        .map(|snapshot| {
            let id = Uuid::new_v4();
            if snapshot["harness_key"].as_str() == Some(state.selected_agent.as_str()) {
                *state.published.lock().unwrap() =
                    Some((id, snapshot["config_revision"].as_str().unwrap().to_owned()));
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

async fn poll(
    State(state): State<ControlServerState>,
    headers: HeaderMap,
) -> Result<Json<Value>, StatusCode> {
    require_auth(&headers)?;
    let published = state.published.lock().unwrap().clone();
    let commands = if let Some((harness_id, revision)) = published
        && !state.command_sent.swap(true, Ordering::SeqCst)
    {
        let payload = serde_json::to_value(RunSpec {
            agent_run_id: state.run_id,
            conversation_id: Uuid::new_v4(),
            harness_id,
            profile_revision: revision,
            model_name: None,
            config_selections: JsonMap::new(),
            system_prompt: "Follow the user's output format exactly.".to_owned(),
            prompt: vec![json!({
                "type": "text",
                "text": "Reply with exactly: LEMMA_PAIRED_AGENT_HOST_STREAM_OK",
            })],
            context: JsonMap::new(),
            mcp: json!({
                "server_name": "lemma",
                "url": "https://unused.invalid/mcp",
                "authorization": "Bearer unused-real-control-e2e-secret",
            }),
            run_deadline: Utc::now() + chrono::Duration::minutes(5),
        })
        .unwrap();
        vec![json!({
            "command_id": Uuid::new_v4(),
            "kind": "START_RUN",
            "created_at": Utc::now(),
            "expires_at": Utc::now() + chrono::Duration::minutes(2),
            "run_id": state.run_id,
            "lease_epoch": 1,
            "payload": payload,
        })]
    } else {
        Vec::new()
    };
    Ok(Json(json!({
        "protocol_version": 2,
        "host_status": "ONLINE",
        "commands": commands,
        "poll_after_ms": 25,
    })))
}

async fn append_events(
    State(state): State<ControlServerState>,
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
    state.events.lock().unwrap().extend(batch.events);
    Ok(Json(response))
}

fn require_auth(headers: &HeaderMap) -> Result<(), StatusCode> {
    (headers
        .get("authorization")
        .and_then(|value| value.to_str().ok())
        == Some(format!("Bearer {HOST_SECRET}").as_str()))
    .then_some(())
    .ok_or(StatusCode::UNAUTHORIZED)
}

async fn run_through_paired_agent_host(source_paths: &HostPaths, agent: &str) {
    let state = ControlServerState {
        host_id: Uuid::new_v4(),
        user_id: Uuid::new_v4(),
        run_id: Uuid::new_v4(),
        selected_agent: agent.to_owned(),
        published: Arc::new(Mutex::new(None)),
        command_sent: Arc::new(AtomicBool::new(false)),
        events: Arc::new(Mutex::new(Vec::new())),
    };
    let app = Router::new()
        .route("/agent-host/pairings:complete", post(pairing))
        .route("/agent-host/harnesses", put(publish))
        .route("/agent-host/poll", post(poll))
        .route("/agent-host/events:append", post(append_events))
        .with_state(state.clone());
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let server = tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });

    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    #[cfg(unix)]
    std::os::unix::fs::symlink(&source_paths.adapters, &paths.adapters).unwrap();
    let installation_id = Uuid::new_v4().to_string();
    let target = TargetClient::pair(
        url::Url::parse(&format!("http://{address}/")).unwrap(),
        "real-control-e2e-pairing-code",
        "Real control E2E",
        &installation_id,
        true,
    )
    .await
    .unwrap();
    let config = HostConfig {
        installation_id,
        targets: vec![target],
        max_runs: 1,
    };
    config.save(&paths).unwrap();

    let runtime = HostRuntime::new(config, paths.clone()).unwrap();
    let runtime =
        runtime.with_mcp_bridge_executable(PathBuf::from(env!("CARGO_BIN_EXE_lemma-agent-host")));
    let runtime_handle = tokio::spawn(runtime.serve());

    let completed = tokio::time::timeout(Duration::from_secs(150), async {
        loop {
            if state
                .events
                .lock()
                .unwrap()
                .iter()
                .any(|event| event.event_type == EventType::Terminal)
            {
                return;
            }
            assert!(
                !runtime_handle.is_finished(),
                "Agent Host runtime exited before the run completed"
            );
            tokio::time::sleep(Duration::from_millis(100)).await;
        }
    })
    .await;
    if completed.is_err() {
        let published = state.published.lock().unwrap().clone();
        let command_sent = state.command_sent.load(Ordering::SeqCst);
        let event_types = state
            .events
            .lock()
            .unwrap()
            .iter()
            .map(|event| event.event_type)
            .collect::<Vec<_>>();
        panic!(
            "paired Agent Host run timed out; published={published:?}, \
             command_sent={command_sent}, events={event_types:?}"
        );
    }
    runtime_handle.abort();
    let _ = runtime_handle.await;
    server.abort();

    let events = state.events.lock().unwrap();
    let answer = events
        .iter()
        .filter(|event| event.event_type == EventType::AgentMessageChunk)
        .filter_map(|event| event.payload.get("text").and_then(Value::as_str))
        .collect::<String>();
    assert!(
        answer.contains("LEMMA_PAIRED_AGENT_HOST_STREAM_OK"),
        "paired Agent Host did not stream the expected answer: {answer:?}"
    );
    assert!(
        events
            .iter()
            .any(|event| event.event_type == EventType::Terminal),
        "paired Agent Host did not durably append a terminal event"
    );
    println!("paired-agent-host: LEMMA_PAIRED_AGENT_HOST_STREAM_OK");
}
