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
                args: vec![
                    fixture.to_string_lossy().into_owned(),
                    log.to_string_lossy().into_owned(),
                ],
                upstream_command: "python3".into(),
                upstream_version_args: vec!["--version".into()],
                upstream_path_env: None,
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
                    context: BTreeMap::new(),
                    mcp: Value::Null,
                    run_deadline: chrono::Utc::now() + chrono::Duration::minutes(1),
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
