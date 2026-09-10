use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};

use std::time::Duration;

use lemma_agent_host::acp::{AcpCallbacks, AcpDriver, AcpRunRequest, AgentDriver};
use lemma_agent_host::adapters::{AdapterSpec, ResolvedAdapter};
use lemma_agent_host::permissions::PermissionGate;
use lemma_agent_host::protocol::{EventType, JsonMap, RunSpec, RunState};
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
    let driver = AcpDriver;
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
                },
                scratch_directory: directory.path().join("run"),
                mcp_server: None,
                can_load_session: false,
                published_config_options: Vec::new(),
                permissions: PermissionGate::new(),
                permission_timeout: Duration::ZERO,
                cancel: lemma_agent_host::acp::never_cancelled(),
                cancel_grace: Duration::from_secs(5),
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
            },
            scratch_directory: cwd.clone(),
            mcp_server: None,
            can_load_session: true,
            published_config_options: Vec::new(),
            permissions: PermissionGate::new(),
            permission_timeout: Duration::ZERO,
            cancel: lemma_agent_host::acp::never_cancelled(),
            cancel_grace: Duration::from_secs(5),
        };
        AcpDriver
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
        AcpDriver
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

    AcpDriver
        .run(
            AcpRunRequest {
                adapter,
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
                },
                scratch_directory: cwd,
                mcp_server: None,
                can_load_session: true,
                published_config_options: Vec::new(),
                permissions: PermissionGate::new(),
                permission_timeout: Duration::ZERO,
                cancel: lemma_agent_host::acp::never_cancelled(),
                cancel_grace: Duration::from_secs(5),
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
