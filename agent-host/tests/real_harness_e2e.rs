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

mod support;

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
                            resume_session_id: None,
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
                        can_load_session: false,
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

/// Drive one turn and return `(session id, what the agent streamed back)`.
async fn one_turn(
    manifest: &AdapterManifest,
    paths: &HostPaths,
    agent: &str,
    conversation_id: Uuid,
    prompt: &str,
    resume_session_id: Option<String>,
) -> (String, String) {
    let callbacks = Arc::new(StreamCapture::default());
    let run_id = Uuid::new_v4();
    let outcome = AcpDriver
        .run(
            AcpRunRequest {
                adapter: manifest.resolve(agent).unwrap(),
                run_spec: RunSpec {
                    agent_run_id: run_id,
                    conversation_id,
                    harness_id: Uuid::new_v4(),
                    profile_revision: "real-continuity-e2e".to_owned(),
                    model_name: None,
                    config_selections: JsonMap::new(),
                    system_prompt: "Answer in one short sentence.".to_owned(),
                    prompt: vec![json!({"type": "text", "text": prompt})],
                    resume_session_id,
                    context: BTreeMap::new(),
                    mcp: Value::Null,
                    run_deadline: Utc::now() + chrono::Duration::minutes(5),
                },
                scratch_directory: paths
                    .root
                    .join("real-continuity")
                    .join(agent)
                    // Both turns share a directory: a provider is entitled to
                    // refuse to load a session into a different cwd, and a
                    // Lemma conversation keeps one workspace anyway.
                    .join(conversation_id.to_string()),
                mcp_server: None,
                can_load_session: true,
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
    let answer = callbacks
        .events
        .lock()
        .unwrap()
        .iter()
        .filter(|(event_type, _)| *event_type == EventType::AgentMessageChunk)
        .filter_map(|(_, payload)| payload.get("text").and_then(Value::as_str))
        .collect::<String>();
    (outcome.provider_session_id, answer)
}

/// One Lemma conversation is one provider session.
///
/// Without this the agent meets the user again on every message: it never sees
/// what it just said, so it re-asks answered questions and contradicts itself.
/// The check is deliberately behavioural rather than structural — that
/// `session/load` was sent proves nothing if the provider ignored it.
#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn a_conversation_keeps_one_provider_session_across_turns() {
    let paths = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(paths.adapters.clone());

    for agent in configured_agents() {
        let conversation_id = Uuid::new_v4();
        let (session_id, _) = one_turn(
            &manifest,
            &paths,
            &agent,
            conversation_id,
            "My name is Ada. Remember it.",
            None,
        )
        .await;

        let started = std::time::Instant::now();
        let (resumed_session_id, answer) = one_turn(
            &manifest,
            &paths,
            &agent,
            conversation_id,
            "What is my name? Reply with just the name.",
            Some(session_id.clone()),
        )
        .await;
        let elapsed = started.elapsed();

        assert!(
            answer.to_lowercase().contains("ada"),
            "{agent} lost the conversation across turns; answer={answer:?}"
        );
        assert_eq!(
            resumed_session_id, session_id,
            "{agent} answered in a different session than the one it was asked to resume"
        );
        // Not a benchmark — a regression alarm. Resolving the adapter used to
        // re-hash the npm package on every run, which put ~5s of pure host
        // overhead in front of a model that answers this in one or two.
        assert!(
            elapsed < Duration::from_secs(30),
            "{agent} took {elapsed:?} for a warm follow-up turn"
        );
        println!("{agent}: resumed {session_id} in {elapsed:?} -> {answer:?}");
    }
}

/// A session the provider no longer has costs history, never the answer.
///
/// Providers expire sessions on their own schedule — Codex prunes rollout
/// files, a Claude Code session can be deleted from disk — so a stored id going
/// stale is normal operation, not an error. If a failed `session/load` failed
/// the run, a conversation would become permanently unusable the day its
/// provider forgot it.
#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn a_session_the_provider_has_forgotten_still_answers() {
    let paths = HostPaths::under(agent_host_data_directory());
    let manifest = AdapterManifest::builtin()
        .unwrap()
        .with_cache_root(paths.adapters.clone());

    for agent in configured_agents() {
        let (session_id, answer) = one_turn(
            &manifest,
            &paths,
            &agent,
            Uuid::new_v4(),
            "Reply with exactly: LEMMA_FALLBACK_OK",
            Some(Uuid::new_v4().to_string()),
        )
        .await;
        assert!(
            answer.contains("LEMMA_FALLBACK_OK"),
            "{agent} did not answer after a failed resume; answer={answer:?}"
        );
        assert!(
            !session_id.is_empty(),
            "{agent} answered without reporting a session to store"
        );
        println!("{agent}: fell back to a new session {session_id}");
    }
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
                resume_session_id: None,
                context: JsonMap::new(),
                mcp: Value::Null,
                run_deadline: Utc::now() + chrono::Duration::minutes(10),
            },
            scratch_directory: scratch.path().to_path_buf(),
            mcp_server: None,
            can_load_session: false,
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

/// Runs one real agent through a paired Agent Host wired to `control`.
///
/// This is the same in-process `HostRuntime` the paired smoke test uses, but
/// pointed at `support::ControlPlane` so the run's MCP endpoint and its
/// permission decisions can be scripted.
async fn paired_real_run(
    agent: &str,
    prompt: &str,
    mcp: Value,
    answer: support::PermissionAnswer,
    budget: Duration,
) -> (TempDir, support::ControlPlane) {
    let source = HostPaths::under(agent_host_data_directory());
    let directory = TempDir::new().unwrap();
    let control = support::ControlPlane::start(agent, prompt, mcp, answer).await;
    let host = support::InProcessHost::start(
        directory.path(),
        &control,
        &source.adapters,
        PathBuf::from(env!("CARGO_BIN_EXE_lemma-agent-host")),
    )
    .await;
    control
        .wait_for("the real agent's run to finish", budget, |control| {
            assert!(!host.is_finished(), "the Agent Host runtime exited early");
            control.saw_terminal()
        })
        .await;
    host.shutdown().await;
    (directory, control)
}

#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn real_agents_discover_and_call_a_lemma_mcp_tool() {
    // The hermetic suite proves the host wires an MCP bridge correctly and that
    // an agent which uses it reaches Lemma. This proves the remaining half that
    // only a real provider can: that a commercial agent, handed the `lemma`
    // server through ACP, actually discovers `lemma_*` tools and calls one.
    //
    // Lemma itself is still a stand-in; the endpoint here speaks the same
    // stateless JSON-RPC-over-HTTP contract `app/mcp_server.py` mounts.
    for agent in configured_agents() {
        let endpoint = support::LemmaMcpEndpoint::start(support::McpTransport::StatelessJson).await;
        let (_directory, control) = paired_real_run(
            &agent,
            concat!(
                "Call the Lemma MCP tool named lemma_echo with the argument ",
                "text set to exactly LEMMA_REAL_MCP_OK. Do not use any other ",
                "tool and do not write files. Reply with exactly the text the ",
                "tool returned."
            ),
            endpoint.run_configuration(),
            support::PermissionAnswer::AllowOnce,
            Duration::from_secs(300),
        )
        .await;

        let methods = endpoint.methods();
        assert!(
            methods.iter().any(|method| method == "tools/list"),
            "{agent} never listed Lemma's tools; methods={methods:?}"
        );
        let call = endpoint
            .requests()
            .into_iter()
            .find(|record| record.method == "tools/call")
            .unwrap_or_else(|| panic!("{agent} never called a Lemma tool; methods={methods:?}"));
        assert_eq!(
            call.params["name"], "lemma_echo",
            "{agent} called the wrong Lemma tool"
        );
        assert_eq!(
            call.agent_run_id.as_deref(),
            Some(control.run_id.to_string().as_str()),
            "{agent}'s tool call was not attributed to its run"
        );
        assert!(
            control.assistant_text().contains("LEMMA_REAL_MCP_OK"),
            "{agent} did not report the Lemma tool result: {:?}",
            control.assistant_text()
        );
        println!("{agent}: LEMMA_REAL_MCP_TOOL_CALL_OK");
    }
}

/// A per-run file outside every adapter's sandbox, plus the shell command that
/// writes it.
///
/// The trigger matters and was chosen from observed behaviour, not guessed.
/// Codex runs sandboxed commands — including `printf` in the workspace and even
/// `curl` to the public internet — without asking anyone, so a prompt like
/// "print this token" proves nothing about approvals. What Codex *does*
/// escalate is a write outside its workspace sandbox, and Claude Code asks for
/// any Bash at all, so a write to a private directory under `$HOME` is the one
/// action both adapters put behind a permission request.
///
/// Using a real filesystem effect rather than the agent's prose is deliberate:
/// a model can claim it ran a command, but it cannot fabricate the file.
struct GatedWrite {
    directory: PathBuf,
    path: PathBuf,
    token: String,
}

impl GatedWrite {
    fn new(agent: &str) -> Self {
        let home = std::env::var_os("HOME")
            .map(PathBuf::from)
            .expect("HOME is required to place a file outside the provider sandbox");
        let directory = home.join(".lemma-agent-host-e2e");
        let token = format!("LEMMA-GATED-{}", Uuid::new_v4().simple());
        Self {
            path: directory.join(format!("{agent}-{token}.txt")),
            directory,
            token,
        }
    }

    fn prompt(&self) -> String {
        format!(
            "Run this exact shell command and nothing else: \
             `mkdir -p {directory} && printf %s {token} > {path}`. \
             Then reply with exactly DONE.",
            directory = self.directory.display(),
            token = self.token,
            path = self.path.display(),
        )
    }

    /// Did the gated command actually run?
    fn happened(&self) -> bool {
        std::fs::read_to_string(&self.path).is_ok_and(|body| body.trim() == self.token)
    }
}

impl Drop for GatedWrite {
    fn drop(&mut self) {
        let _ = std::fs::remove_file(&self.path);
        // Only succeeds once the last concurrent run has cleaned up.
        let _ = std::fs::remove_dir(&self.directory);
    }
}

#[tokio::test]
#[ignore = "requires authenticated local agents, spends real provider quota, and writes a self-cleaning file under $HOME"]
async fn a_real_agents_native_tool_waits_for_lemmas_approval() {
    // The approval round trip against each real adapter's own permission shape.
    // The command cannot run unless Lemma's decision reached the parked ACP
    // responder, so the file existing afterwards is proof that the agent was
    // released and continued rather than being cancelled or left hanging.
    for agent in configured_agents() {
        let gated = GatedWrite::new(&agent);
        assert!(!gated.happened(), "the gated file must not exist up front");
        let (_directory, control) = paired_real_run(
            &agent,
            &gated.prompt(),
            json!({
                "server_name": "lemma_tools",
                "url": "https://unused.invalid/mcp",
                "authorization": "Bearer unused-real-permission-e2e",
            }),
            support::PermissionAnswer::AllowOnce,
            Duration::from_secs(300),
        )
        .await;

        let requests = control.permission_requests();
        assert!(
            !requests.is_empty(),
            "{agent} wrote outside its sandbox without ever asking Lemma; a \
             harness that never parks a request is not gated at all"
        );
        // Lemma renders these as approval cards and addresses its decision to
        // the request id, so a request missing either is unanswerable.
        for request in &requests {
            assert!(
                request.object_id.is_some(),
                "{agent}'s permission request has no id for a decision to name"
            );
            assert!(
                request.payload.contains_key("options"),
                "{agent}'s permission request offered no options to choose between"
            );
        }
        assert!(
            gated.happened(),
            "{agent} never ran the approved command, so the approval did not \
             reach it; answer={:?}",
            control.assistant_text()
        );
        println!(
            "{agent}: LEMMA_REAL_PERMISSION_APPROVED_OK ({} request(s))",
            requests.len()
        );
    }
}

#[tokio::test]
#[ignore = "requires authenticated local agents and spends real provider quota"]
async fn a_real_agents_denied_tool_is_stopped_without_waiting_out_the_timeout() {
    // A denial has to travel the same path an approval does, and it has to
    // actually stop the tool. If the decision never arrived the agent would
    // block for the full thirty-minute permission timeout, which a user cannot
    // tell apart from a hung run.
    for agent in configured_agents() {
        let gated = GatedWrite::new(&agent);
        let started = std::time::Instant::now();
        let (_directory, control) = paired_real_run(
            &agent,
            &gated.prompt(),
            json!({
                "server_name": "lemma_tools",
                "url": "https://unused.invalid/mcp",
                "authorization": "Bearer unused-real-permission-e2e",
            }),
            support::PermissionAnswer::Deny,
            Duration::from_secs(300),
        )
        .await;

        assert!(
            !control.permission_requests().is_empty(),
            "{agent} never asked, so there was nothing to deny"
        );
        // A blocked command is also what a host that denies everything by
        // itself produces, so require that Lemma got to decide while the agent
        // was still waiting.
        let decisions = control.decisions();
        assert!(
            !decisions.is_empty() && decisions.iter().any(|decision| !decision.saw_terminal),
            "{agent}'s run was already over before Lemma answered, so this \
             denial did not come from Lemma: {decisions:?}"
        );
        assert!(!gated.happened(), "{agent} ran the command Lemma refused");
        assert!(
            started.elapsed() < Duration::from_secs(300),
            "{agent} waited out the permission timeout instead of being denied"
        );
        let terminal = control
            .events()
            .into_iter()
            .find(|event| event.event_type == EventType::Terminal)
            .unwrap();
        assert_ne!(
            terminal.payload["state"], "DISPATCH_UNKNOWN",
            "{agent}'s denied run must reach a definite outcome"
        );
        println!(
            "{agent}: LEMMA_REAL_PERMISSION_DENIED_OK in {:?} ({})",
            started.elapsed(),
            terminal.payload["state"]
        );
    }
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
            resume_session_id: None,
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
    // Paired the way Desktop pairs: over `app.lemma.localhost`, the hostname the
    // app serves its own workspace and API on, rather than the raw loopback IP.
    // Using `127.0.0.1` here is what let this test pass while "Connect this
    // computer" was impossible in the product — that hostname was refused as a
    // non-loopback plain-HTTP target by both locald and the host.
    let target = TargetClient::pair(
        url::Url::parse(&format!("http://app.lemma.localhost:{}/", address.port())).unwrap(),
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
