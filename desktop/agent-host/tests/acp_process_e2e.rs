use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

use std::time::Duration;

use lemma_agent_host::acp::{AcpCallbacks, AcpDriver, AcpRunRequest, AgentDriver};
use lemma_agent_host::adapters::{AdapterSpec, ResolvedAdapter};
use lemma_agent_host::permissions::PermissionGate;
use lemma_agent_host::protocol::{
    ConfigOption, EventType, JsonMap, MODEL_OPTION_SET_KEY, RunSpec, RunState,
};
use serde_json::Value;
use tempfile::TempDir;
use uuid::Uuid;

#[derive(Default)]
struct CapturingCallbacks {
    dispatched: AtomicBool,
    events: Mutex<Vec<(EventType, JsonMap)>>,
}

impl AcpCallbacks for CapturingCallbacks {
    fn before_prompt(&self, provider_session_id: &str) -> anyhow::Result<()> {
        assert_eq!(provider_session_id, "fake-session");
        self.dispatched.store(true, Ordering::SeqCst);
        Ok(())
    }

    fn event(
        &self,
        event_type: EventType,
        _object_id: Option<String>,
        payload: JsonMap,
    ) -> anyhow::Result<()> {
        self.events.lock().unwrap().push((event_type, payload));
        Ok(())
    }
}

fn python() -> PathBuf {
    let executable_names = if cfg!(windows) {
        &["python.exe", "python3.exe"][..]
    } else {
        &["python3", "python"][..]
    };
    std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
        .find_map(|path| {
            executable_names
                .iter()
                .map(|name| path.join(name))
                .find(|candidate| candidate.is_file())
        })
        .expect("Python is required for the ACP process integration test")
}

fn fake_adapter(directory: &TempDir) -> (ResolvedAdapter, PathBuf) {
    fake_adapter_in_mode(directory, None)
}

fn fake_adapter_in_mode(directory: &TempDir, mode: Option<&str>) -> (ResolvedAdapter, PathBuf) {
    let log = directory.path().join("messages.jsonl");
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("fake_acp_agent.py");
    (
        ResolvedAdapter {
            spec: AdapterSpec {
                key: "fake".into(),
                display_name: "Fake".into(),
                adapter_version: "1.0.0".into(),
                command: "python3".into(),
                args: [
                    fixture.to_string_lossy().into_owned(),
                    log.to_string_lossy().into_owned(),
                ]
                .into_iter()
                .chain(mode.map(str::to_owned))
                .collect(),
                upstream_command: "python3".into(),
                upstream_version_args: vec!["--version".into()],
                upstream_path_env: None,
                environment: BTreeMap::default(),
                omit_optional_dependencies: false,
                minimum_upstream_version: None,
                distribution: "native".into(),
                artifact_integrity: None,
                license: "test".into(),
            },
            command: python(),
            upstream_command: python(),
            upstream_version: Some("test".into()),
        },
        log,
    )
}

#[tokio::test]
async fn official_sdk_negotiates_probes_config_and_streams_a_prompt() {
    let directory = TempDir::new().unwrap();
    let (adapter, log) = fake_adapter(&directory);
    let driver = AcpDriver::default();
    let probe = driver
        .probe(adapter.clone(), directory.path().join("probe"))
        .await
        .unwrap();
    assert_eq!(probe.config_options.len(), 1);
    assert_eq!(probe.config_options[0].category, "model");
    assert_eq!(
        probe.config_options[0].current_value,
        Value::String("fake-1".into())
    );
    assert_eq!(probe.config_options[0].options.len(), 2);

    let callbacks = std::sync::Arc::new(CapturingCallbacks::default());
    let outcome = driver
        .run(
            AcpRunRequest {
                adapter,
                agent_environment: std::collections::BTreeMap::default(),
                own_settings: false,
                lemma_cli_bin: None,
                run_spec: RunSpec {
                    agent_run_id: Uuid::new_v4(),
                    conversation_id: Uuid::new_v4(),
                    harness_id: Uuid::new_v4(),
                    profile_revision: "dynamic-revision".into(),
                    model_name: Some("fake-2".into()),
                    config_selections: JsonMap::new(),
                    system_prompt: "Be concise.".into(),
                    prompt: vec![serde_json::json!({"type": "text", "text": "Say hello"})],
                    resume_session_id: None,
                    workspace_cwd: None,
                    context: BTreeMap::new(),
                    mcp: Value::Null,
                    run_deadline: chrono::Utc::now() + chrono::Duration::minutes(1),
                    system_prompt_delivery: None,
                    pre_approvals: Vec::new(),
                },
                scratch_directory: directory.path().join("run"),
                mcp_server: None,
                can_load_session: false,
                published_config_options: Vec::new(),
                permissions: PermissionGate::new(),
                permission_timeout: Duration::ZERO,
                cancel: lemma_agent_host::acp::never_cancelled(),
                cancel_grace: Duration::from_secs(5),
                steer: lemma_agent_host::acp::SteerInbox::default(),
            },
            callbacks.clone(),
        )
        .await
        .unwrap();
    assert_eq!(outcome.state, RunState::Succeeded);
    assert!(callbacks.dispatched.load(Ordering::SeqCst));
    let events = callbacks.events.lock().unwrap();
    assert!(
        events
            .iter()
            .any(|(kind, _)| *kind == EventType::PermissionRequest)
    );
    assert!(events.iter().any(|(kind, payload)| {
        *kind == EventType::AgentMessageChunk
            && payload.get("text") == Some(&Value::String("hello from fake".into()))
    }));
    drop(events);

    let messages = std::fs::read_to_string(log).unwrap();
    assert!(messages.contains("\"method\":\"session/set_config_option\""));
    assert!(messages.contains("\"configId\":\"model\""));
    assert!(messages.contains("\"value\":\"fake-2\""));
    assert!(messages.contains("<system>\\nBe concise.\\n</system>"));
    assert!(messages.contains("\"id\":900,\"result\":{\"outcome\":{\"outcome\":\"cancelled\"}}"));
}

#[tokio::test]
async fn every_harness_opens_and_resumes_in_the_same_saved_directory() {
    for harness in ["claude-code", "codex", "opencode"] {
        let directory = TempDir::new().unwrap();
        let (mut adapter, log) = fake_adapter(&directory);
        adapter.spec.key = harness.to_owned();
        let cwd = directory.path().join("lemma/c/2026-09-07/Δ project");
        let request = AcpRunRequest {
            adapter,
            agent_environment: std::collections::BTreeMap::default(),
            own_settings: false,
            lemma_cli_bin: None,
            run_spec: RunSpec {
                agent_run_id: Uuid::new_v4(),
                conversation_id: Uuid::new_v4(),
                harness_id: Uuid::new_v4(),
                profile_revision: "revision".into(),
                model_name: None,
                config_selections: JsonMap::new(),
                system_prompt: "Be concise".into(),
                prompt: vec![serde_json::json!({"type": "text", "text": "hello"})],
                resume_session_id: None,
                workspace_cwd: Some("/workspace/c/2026-09-07/Δ project".into()),
                context: BTreeMap::new(),
                mcp: Value::Null,
                run_deadline: chrono::Utc::now() + chrono::Duration::minutes(1),
                system_prompt_delivery: None,
                pre_approvals: Vec::new(),
            },
            scratch_directory: cwd.clone(),
            mcp_server: None,
            can_load_session: true,
            published_config_options: Vec::new(),
            permissions: PermissionGate::new(),
            permission_timeout: Duration::ZERO,
            cancel: lemma_agent_host::acp::never_cancelled(),
            cancel_grace: Duration::from_secs(5),
            steer: lemma_agent_host::acp::SteerInbox::default(),
        };
        AcpDriver::default()
            .run(
                request.clone(),
                std::sync::Arc::new(CapturingCallbacks::default()),
            )
            .await
            .unwrap();
        std::fs::write(cwd.join("work.txt"), "first turn").unwrap();
        let mut resumed = request;
        resumed.run_spec.agent_run_id = Uuid::new_v4();
        resumed.run_spec.resume_session_id = Some("fake-session".into());
        AcpDriver::default()
            .run(resumed, std::sync::Arc::new(CapturingCallbacks::default()))
            .await
            .unwrap();
        assert_eq!(
            std::fs::read_to_string(cwd.join("work.txt")).unwrap(),
            "first turn"
        );
        let traffic: Vec<Value> = std::fs::read_to_string(log)
            .unwrap()
            .lines()
            .map(|line| serde_json::from_str(line).unwrap())
            .collect();
        let sessions: Vec<_> = traffic
            .iter()
            .filter(|message| {
                matches!(
                    message["method"].as_str(),
                    Some("session/new" | "session/load")
                )
            })
            .collect();
        assert_eq!(sessions.len(), 2, "{harness}");
        assert_eq!(sessions[0]["method"], "session/new");
        assert_eq!(sessions[1]["method"], "session/load");
        assert_eq!(sessions[1]["params"]["sessionId"], "fake-session");
        for session in sessions {
            assert_eq!(session["params"]["cwd"], cwd.to_str().unwrap(), "{harness}");
        }
    }
}

/// A conversation the provider has forgotten is answered, and said so.
///
/// The case Lemma cannot predict: it leaves the conversation's history out of
/// the prompt exactly when it expects `session/load` to supply it, and a
/// provider is free to have pruned the session by then. The turn used to be
/// answered as though nothing had happened -- a confident reply to a question
/// about something the agent had never seen, with the only record a `warn!` on
/// the user's own machine.
#[tokio::test]
async fn a_forgotten_session_is_answered_and_reported_rather_than_silently_lost() {
    let directory = TempDir::new().unwrap();
    let (adapter, log) = fake_adapter_in_mode(&directory, Some("forget-session"));
    let callbacks = std::sync::Arc::new(CapturingCallbacks::default());
    let cwd = directory.path().join("cwd");
    std::fs::create_dir_all(&cwd).unwrap();

    AcpDriver::default()
        .run(
            AcpRunRequest {
                adapter,
                agent_environment: std::collections::BTreeMap::default(),
                own_settings: false,
                lemma_cli_bin: None,
                run_spec: RunSpec {
                    agent_run_id: Uuid::new_v4(),
                    conversation_id: Uuid::new_v4(),
                    harness_id: Uuid::new_v4(),
                    profile_revision: "r".into(),
                    model_name: None,
                    config_selections: JsonMap::new(),
                    system_prompt: "Be concise.".into(),
                    prompt: vec![serde_json::json!({"type": "text", "text": "and then?"})],
                    // Lemma expects this to be resumed, so the prompt above is
                    // the whole of what the agent is given.
                    resume_session_id: Some("fake-session".into()),
                    workspace_cwd: None,
                    context: JsonMap::new(),
                    mcp: serde_json::Value::Null,
                    run_deadline: chrono::Utc::now() + chrono::Duration::minutes(1),
                    system_prompt_delivery: Some(
                        lemma_agent_host::protocol::NEW_SESSION_ONLY.to_owned(),
                    ),
                    pre_approvals: Vec::new(),
                },
                scratch_directory: cwd,
                mcp_server: None,
                can_load_session: true,
                published_config_options: Vec::new(),
                permissions: PermissionGate::new(),
                permission_timeout: Duration::ZERO,
                cancel: lemma_agent_host::acp::never_cancelled(),
                cancel_grace: Duration::from_secs(5),
                steer: lemma_agent_host::acp::SteerInbox::default(),
            },
            callbacks.clone(),
        )
        .await
        .expect("the turn is still answered");

    let traffic: Vec<Value> = std::fs::read_to_string(log)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    let methods: Vec<&str> = traffic
        .iter()
        .filter_map(|message| message["method"].as_str())
        .collect();
    assert!(
        methods.contains(&"session/load") && methods.contains(&"session/new"),
        "the load is attempted and the fresh session replaces it: {methods:?}"
    );

    let prompt = traffic
        .iter()
        .find(|message| message["method"] == "session/prompt")
        .expect("the turn was prompted");
    let text = serde_json::to_string(&prompt["params"]).unwrap();
    assert!(
        text.contains("could not be recovered"),
        "the agent has to be told it is missing the conversation: {text}"
    );
    assert!(
        text.contains("Be concise."),
        "and a session that has never seen the instructions gets them: {text}"
    );

    let events = callbacks.events.lock().unwrap();
    let reported = events
        .iter()
        .find(|(_, payload)| payload.get("status") == Some(&Value::from("session_lost")))
        .expect("Lemma is told which session was lost, not just the log file");
    assert_eq!(reported.1["requested_session"], "fake-session");
}

/// The probe of an agent whose models each offer their own reasoning levels,
/// and the value of each model on its published model option.
async fn probe_per_model_options(directory: &TempDir) -> (Vec<JsonMap>, Vec<ConfigOption>) {
    let (adapter, _log) = fake_adapter_in_mode(directory, Some("per-model-options"));
    let probe = AcpDriver::default()
        .probe(adapter, directory.path().join("probe"))
        .await
        .expect("a model that cannot be read does not fail the probe");
    let model = probe
        .config_options
        .iter()
        .find(|option| option.category == "model")
        .expect("the model option is published")
        .options
        .clone();
    (model, probe.config_options)
}

fn model_entry<'a>(models: &'a [JsonMap], value: &str) -> &'a JsonMap {
    models
        .iter()
        .find(|entry| entry.get("value") == Some(&Value::from(value)))
        .unwrap_or_else(|| panic!("{value} is published: {models:?}"))
}

/// The levels an effort option offers, from its published JSON.
fn levels(option: &Value) -> Vec<&str> {
    option["options"]
        .as_array()
        .unwrap()
        .iter()
        .map(|level| level["value"].as_str().unwrap())
        .collect()
}

/// `session/new` describes the default model only. Each model carries the
/// options it offers once chosen, and the top level stays the default's.
#[tokio::test]
async fn the_probe_publishes_each_models_own_options() {
    let directory = TempDir::new().unwrap();
    let (models, top_level) = probe_per_model_options(&directory).await;

    let second = model_entry(&models, "fake-2")[MODEL_OPTION_SET_KEY]
        .as_array()
        .expect("fake-2 has its own options");
    assert_eq!(
        second.len(),
        1,
        "the model option is not nested: {second:?}"
    );
    assert_eq!(second[0]["category"], "thought_level");
    assert_eq!(levels(&second[0]), ["low", "high", "max"]);

    let first = model_entry(&models, "fake-1")[MODEL_OPTION_SET_KEY]
        .as_array()
        .expect("the default model has its own options too");
    assert_eq!(levels(&first[0]), ["low", "medium"]);

    let effort = top_level
        .iter()
        .find(|option| option.category == "thought_level")
        .expect("the default's effort is still at the top level");
    assert_eq!(effort.current_value, "medium");
    assert_eq!(effort.options.len(), 2);
    let model = top_level
        .iter()
        .find(|option| option.category == "model")
        .unwrap();
    assert_eq!(model.current_value, "fake-1");
}

/// A model the agent will not switch to is published without a set of its
/// own -- the same as the top level -- and every other model keeps its own.
/// The default is put back afterwards, so the probe does not move it.
#[tokio::test]
async fn a_model_whose_options_cannot_be_read_is_published_with_the_defaults() {
    let directory = TempDir::new().unwrap();
    let (models, _) = probe_per_model_options(&directory).await;

    assert!(
        !model_entry(&models, "fake-broken").contains_key(MODEL_OPTION_SET_KEY),
        "absent means the top level's options: {models:?}"
    );
    assert!(model_entry(&models, "fake-1").contains_key(MODEL_OPTION_SET_KEY));
    assert!(model_entry(&models, "fake-2").contains_key(MODEL_OPTION_SET_KEY));

    let log = directory.path().join("messages.jsonl");
    let chosen: Vec<String> = std::fs::read_to_string(log)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .filter(|message| message["method"] == "session/set_config_option")
        .map(|message| message["params"]["value"].as_str().unwrap().to_owned())
        .collect();
    assert_eq!(chosen, ["fake-1", "fake-2", "fake-broken", "fake-1"]);
}

/// An agent slow to switch models cannot run the probe out of time: the models
/// asked within the budget keep their own options, the rest are published with
/// the top level's, and the probe still answers well inside its 20s.
#[tokio::test]
async fn a_slow_agents_model_options_stop_at_the_budget_rather_than_failing_the_probe() {
    let directory = TempDir::new().unwrap();
    let (adapter, _log) = fake_adapter_in_mode(&directory, Some("slow-model-options"));
    let started = std::time::Instant::now();
    let probe = AcpDriver::default()
        .probe(adapter, directory.path().join("probe"))
        .await
        .expect("a slow agent still probes");
    assert!(
        started.elapsed() < Duration::from_secs(20),
        "the probe must finish inside refresh_harnesses' 20s: {:?}",
        started.elapsed()
    );
    let models = &probe
        .config_options
        .iter()
        .find(|option| option.category == "model")
        .unwrap()
        .options;
    assert!(model_entry(models, "slow-1").contains_key(MODEL_OPTION_SET_KEY));
    assert!(
        !model_entry(models, "slow-6").contains_key(MODEL_OPTION_SET_KEY),
        "a model past the budget keeps the top level's options: {models:?}"
    );
}

/// A run of `adapter` with nothing special about it, working in `cwd`.
fn plain_run(adapter: ResolvedAdapter, cwd: PathBuf) -> AcpRunRequest {
    AcpRunRequest {
        adapter,
        agent_environment: std::collections::BTreeMap::default(),
        own_settings: false,
        lemma_cli_bin: None,
        run_spec: RunSpec {
            agent_run_id: Uuid::new_v4(),
            conversation_id: Uuid::new_v4(),
            harness_id: Uuid::new_v4(),
            profile_revision: "r".into(),
            model_name: None,
            config_selections: JsonMap::new(),
            system_prompt: String::new(),
            prompt: vec![serde_json::json!({"type": "text", "text": "Reply with exactly: OK"})],
            resume_session_id: None,
            workspace_cwd: None,
            context: JsonMap::new(),
            mcp: Value::Null,
            run_deadline: chrono::Utc::now() + chrono::Duration::minutes(2),
            system_prompt_delivery: None,
            pre_approvals: Vec::new(),
        },
        scratch_directory: cwd,
        mcp_server: None,
        can_load_session: false,
        published_config_options: Vec::new(),
        permissions: PermissionGate::new(),
        permission_timeout: Duration::ZERO,
        cancel: lemma_agent_host::acp::never_cancelled(),
        cancel_grace: Duration::from_secs(5),
        steer: lemma_agent_host::acp::SteerInbox::default(),
    }
}

/// How many agent processes were started against `log`.
fn processes_started(log: &Path) -> usize {
    std::fs::read_to_string(log)
        .unwrap_or_default()
        .lines()
        .filter_map(|line| serde_json::from_str::<Value>(line).ok())
        .filter(|message| message["method"] == "initialize")
        .count()
}

/// Several conversations started at once on Codex used to fail together:
/// each run cold-starts its own Codex, and copies opening a session side by
/// side time out inside Codex. The fake agent fails exactly that way whenever
/// two copies open a session at the same time, so every run answering -- and
/// no copy having needed a second start -- is the start-up gate letting one
/// through at a time.
#[tokio::test]
async fn a_burst_of_runs_on_one_agent_all_start_and_answer() {
    let directory = TempDir::new().unwrap();
    let (mut adapter, log) = fake_adapter_in_mode(&directory, Some("contended"));
    adapter.spec.key = "codex".to_owned();
    let driver = AcpDriver::default();
    let burst = 5;

    let runs = (0..burst).map(|index| {
        let driver = driver.clone();
        let request = plain_run(
            adapter.clone(),
            directory.path().join(format!("run-{index}")),
        );
        async move {
            driver
                .run(request, std::sync::Arc::new(CapturingCallbacks::default()))
                .await
        }
    });
    let outcomes = futures_util::future::join_all(runs).await;

    for outcome in outcomes {
        let outcome = outcome.expect("every run in the burst starts");
        assert_eq!(outcome.state, RunState::Succeeded);
    }
    assert_eq!(
        processes_started(&log),
        burst,
        "queued starts never collided, so none had to be tried again"
    );
}

/// A start that timed out is the shape a busy computer produces, and nothing
/// was asked of the agent yet, so it is started once more rather than failed.
#[tokio::test]
async fn a_start_that_timed_out_is_tried_once_more() {
    let directory = TempDir::new().unwrap();
    let (adapter, log) = fake_adapter_in_mode(&directory, Some("times-out-once"));
    let callbacks = std::sync::Arc::new(CapturingCallbacks::default());

    let outcome = AcpDriver::default()
        .run(
            plain_run(adapter, directory.path().join("run")),
            callbacks.clone(),
        )
        .await
        .expect("the second start answers");

    assert_eq!(outcome.state, RunState::Succeeded);
    assert!(callbacks.dispatched.load(Ordering::SeqCst));
    assert_eq!(processes_started(&log), 2);
}

/// A failure no retry can fix is reported at once, from the one start.
#[tokio::test]
async fn a_misconfigured_agent_is_not_started_twice() {
    let directory = TempDir::new().unwrap();
    let (adapter, log) = fake_adapter_in_mode(&directory, Some("misconfigured"));
    let callbacks = std::sync::Arc::new(CapturingCallbacks::default());

    let error = AcpDriver::default()
        .run(
            plain_run(adapter, directory.path().join("run")),
            callbacks.clone(),
        )
        .await
        .expect_err("a configuration error fails the run");

    assert!(
        error.to_string().contains("unknown model provider"),
        "the agent's own reason is kept: {error}"
    );
    assert!(!callbacks.dispatched.load(Ordering::SeqCst));
    assert_eq!(processes_started(&log), 1);
}

/// An agent released after this host, through the real driver: what it adds
/// is skipped, what the host knows still arrives, and a permission it offers
/// only in a kind the host does not know is refused, never allowed.
#[tokio::test]
async fn a_newer_agent_runs_and_its_unknowns_are_skipped_not_fatal() {
    let directory = TempDir::new().unwrap();
    let (adapter, log) = fake_adapter_in_mode(&directory, Some("future"));
    let callbacks = std::sync::Arc::new(CapturingCallbacks::default());
    let outcome = AcpDriver::default()
        .run(
            AcpRunRequest {
                adapter,
                agent_environment: std::collections::BTreeMap::default(),
                own_settings: false,
                lemma_cli_bin: None,
                run_spec: RunSpec {
                    agent_run_id: Uuid::new_v4(),
                    conversation_id: Uuid::new_v4(),
                    harness_id: Uuid::new_v4(),
                    profile_revision: "revision".into(),
                    model_name: None,
                    config_selections: JsonMap::new(),
                    system_prompt: String::new(),
                    prompt: vec![serde_json::json!({"type": "text", "text": "hello"})],
                    resume_session_id: None,
                    workspace_cwd: None,
                    context: BTreeMap::new(),
                    mcp: Value::Null,
                    run_deadline: chrono::Utc::now() + chrono::Duration::minutes(1),
                    system_prompt_delivery: None,
                    pre_approvals: Vec::new(),
                },
                scratch_directory: directory.path().join("run"),
                mcp_server: None,
                can_load_session: false,
                published_config_options: Vec::new(),
                permissions: PermissionGate::new(),
                permission_timeout: Duration::ZERO,
                cancel: lemma_agent_host::acp::never_cancelled(),
                cancel_grace: Duration::from_secs(5),
                steer: lemma_agent_host::acp::SteerInbox::default(),
            },
            callbacks.clone(),
        )
        .await
        .expect("a newer agent's turn ends as a turn, not a protocol error");
    // A stop reason ACP did not have is reported by name.
    assert_eq!(outcome.state, RunState::Failed);
    assert_eq!(outcome.stop_reason, "budget_exhausted");
    assert!(outcome.message.unwrap().contains("budget_exhausted"));

    let events = callbacks.events.lock().unwrap();
    let chunks: Vec<_> = events
        .iter()
        .filter(|(kind, _)| *kind == EventType::AgentMessageChunk)
        .collect();
    assert_eq!(chunks.len(), 1, "only the chunk this host can read");
    assert_eq!(chunks[0].1["text"], "hello from fake");
    // Only the request offering a known option was put to anyone, and the
    // unknown option it also offered was not part of the card.
    let asked: Vec<_> = events
        .iter()
        .filter(|(kind, _)| *kind == EventType::PermissionRequest)
        .collect();
    assert_eq!(asked.len(), 1);
    assert_eq!(asked[0].1["options"].as_array().unwrap().len(), 1);
    let usage = events
        .iter()
        .find(|(kind, _)| *kind == EventType::Usage)
        .expect("the turn's usage, read count by count");
    assert_eq!(usage.1["output_tokens"], 2);
    drop(events);

    let messages = std::fs::read_to_string(log).unwrap();
    assert!(messages.contains("\"id\":901,\"result\":{\"outcome\":{\"outcome\":\"cancelled\"}}"));
    assert!(!messages.contains("\"optionId\":\"forever\"}"));
}
