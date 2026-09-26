//! What each coding agent is started with beyond ACP, as the agent sees it.
//!
//! The real driver against `fake_acp_agent.py`, which records every message it
//! is sent and the environment it was started in. The switches themselves are
//! verified against each adapter's source in `acp::session_options`; this holds
//! the host to delivering them: Claude Code's in the session's `_meta` (on a
//! new session and a resumed one alike), Codex's and `OpenCode`'s in the
//! process environment, and nothing of the kind when the person chose the
//! agent's own skills and settings.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use lemma_agent_host::acp::{AcpCallbacks, AcpDriver, AcpRunRequest, AgentDriver};
use lemma_agent_host::adapters::{AdapterSpec, ResolvedAdapter};
use lemma_agent_host::permissions::PermissionGate;
use lemma_agent_host::protocol::{EventType, JsonMap, RunSpec};
use serde_json::{Value, json};
use tempfile::TempDir;
use uuid::Uuid;

struct Quiet;

impl AcpCallbacks for Quiet {
    fn before_prompt(&self, _provider_session_id: &str) -> anyhow::Result<()> {
        Ok(())
    }

    fn event(
        &self,
        _event_type: EventType,
        _object_id: Option<String>,
        _payload: JsonMap,
    ) -> anyhow::Result<()> {
        Ok(())
    }
}

fn python() -> PathBuf {
    std::env::split_paths(&std::env::var_os("PATH").unwrap_or_default())
        .flat_map(|path| ["python3", "python"].map(|name| path.join(name)))
        .find(|candidate| candidate.is_file())
        .expect("Python is required for the ACP process integration test")
}

fn adapter(key: &str, log: &Path, environment: BTreeMap<String, String>) -> ResolvedAdapter {
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/fake_acp_agent.py");
    ResolvedAdapter {
        spec: AdapterSpec {
            key: key.into(),
            display_name: key.into(),
            adapter_version: "1.0.0".into(),
            command: "python3".into(),
            args: vec![
                fixture.to_string_lossy().into_owned(),
                log.to_string_lossy().into_owned(),
            ],
            upstream_command: "python3".into(),
            upstream_version_args: vec!["--version".into()],
            upstream_path_env: None,
            environment,
            omit_optional_dependencies: false,
            minimum_upstream_version: None,
            distribution: "native".into(),
            artifact_integrity: None,
            license: "test".into(),
        },
        command: python(),
        upstream_command: python(),
        upstream_version: Some("test".into()),
    }
}

fn request(
    adapter: ResolvedAdapter,
    cwd: PathBuf,
    own_settings: bool,
    mcp: Value,
) -> AcpRunRequest {
    AcpRunRequest {
        adapter,
        agent_environment: BTreeMap::default(),
        own_settings,
        run_spec: RunSpec {
            agent_run_id: Uuid::new_v4(),
            conversation_id: Uuid::new_v4(),
            harness_id: Uuid::new_v4(),
            profile_revision: "r".into(),
            model_name: None,
            config_selections: JsonMap::new(),
            system_prompt: "You are Lemma's agent.".into(),
            prompt: vec![json!({"type": "text", "text": "hello"})],
            resume_session_id: None,
            workspace_cwd: None,
            context: JsonMap::new(),
            mcp,
            run_deadline: chrono::Utc::now() + chrono::Duration::minutes(1),
            system_prompt_delivery: None,
        },
        scratch_directory: cwd,
        mcp_server: None,
        can_load_session: true,
        published_config_options: Vec::new(),
        permissions: PermissionGate::new(),
        permission_timeout: Duration::ZERO,
        cancel: lemma_agent_host::acp::never_cancelled(),
        cancel_grace: Duration::from_secs(5),
    }
}

/// Every line the agent recorded.
fn read_traffic(log: &Path) -> Vec<Value> {
    std::fs::read_to_string(log)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect()
}

fn sessions(traffic: &[Value]) -> Vec<&Value> {
    traffic
        .iter()
        .filter(|message| {
            matches!(
                message["method"].as_str(),
                Some("session/new" | "session/load")
            )
        })
        .collect()
}

fn prompts(traffic: &[Value]) -> Vec<String> {
    traffic
        .iter()
        .filter(|message| message["method"] == "session/prompt")
        .map(|message| message["params"]["prompt"].to_string())
        .collect()
}

fn recorded_environment(traffic: &[Value]) -> &serde_json::Map<String, Value> {
    traffic
        .iter()
        .find_map(|line| line["environment"].as_object())
        .expect("the agent recorded its environment")
}

#[tokio::test]
async fn claude_code_gets_lemmas_instructions_and_settings_in_the_sessions_meta() {
    let directory = TempDir::new().unwrap();
    let log = directory.path().join("claude.jsonl");
    let cwd = directory.path().join("cwd");
    let mcp = json!({ "tool_names": ["lemma_web_search", "lemma_ask_user"] });
    let first = request(
        adapter("claude-code", &log, BTreeMap::new()),
        cwd.clone(),
        false,
        mcp,
    );
    AcpDriver.run(first.clone(), Arc::new(Quiet)).await.unwrap();
    let mut resumed = first;
    resumed.run_spec.resume_session_id = Some("fake-session".into());
    AcpDriver.run(resumed, Arc::new(Quiet)).await.unwrap();

    let traffic = read_traffic(&log);
    let opened = sessions(&traffic);
    assert_eq!(opened.len(), 2);
    for session in opened {
        let meta = &session["params"]["_meta"];
        assert_eq!(
            meta["systemPrompt"],
            json!({ "append": "You are Lemma's agent." }),
            "{session}"
        );
        let options = &meta["claudeCode"]["options"];
        assert_eq!(options["settingSources"], json!(["project", "local"]));
        assert_eq!(options["strictMcpConfig"], json!(true));
        assert_eq!(
            options["env"]["CLAUDE_CODE_DISABLE_AUTO_MEMORY"],
            json!("1")
        );
        let disallowed = options["disallowedTools"].as_array().unwrap();
        assert!(disallowed.contains(&json!("mcp__claude-in-chrome")));
        // Lemma serves web search on this run, and not a page fetch.
        assert!(disallowed.contains(&json!("WebSearch")));
        assert!(!disallowed.contains(&json!("WebFetch")));
    }
    // Given in the session, so never again as the person's own words.
    for prompt in prompts(&traffic) {
        assert!(!prompt.contains("<system>"), "{prompt}");
        assert!(!prompt.contains("You are Lemma's agent."), "{prompt}");
    }
}

#[tokio::test]
async fn claude_code_with_its_own_settings_keeps_them() {
    let directory = TempDir::new().unwrap();
    let log = directory.path().join("claude.jsonl");
    let run = request(
        adapter("claude-code", &log, BTreeMap::new()),
        directory.path().join("cwd"),
        true,
        Value::Null,
    );
    AcpDriver.run(run, Arc::new(Quiet)).await.unwrap();

    let traffic = read_traffic(&log);
    let options = &sessions(&traffic)[0]["params"]["_meta"]["claudeCode"]["options"];
    assert!(options.get("settingSources").is_none(), "{options}");
    assert!(options.get("strictMcpConfig").is_none(), "{options}");
    // Lemma's browser is the one the person watches, whatever the setting.
    assert!(
        options["disallowedTools"]
            .as_array()
            .unwrap()
            .contains(&json!("mcp__claude-in-chrome"))
    );
}

#[tokio::test]
async fn codex_gets_its_switches_merged_into_the_pinned_config() {
    let directory = TempDir::new().unwrap();
    let log = directory.path().join("codex.jsonl");
    let pinned = BTreeMap::from([(
        "CODEX_CONFIG".to_owned(),
        json!({ "plugins": { "browser@openai-bundled": { "enabled": false } } }).to_string(),
    )]);
    let run = request(
        adapter("codex", &log, pinned),
        directory.path().join("cwd"),
        false,
        json!({ "tool_names": ["lemma_web_search"] }),
    );
    AcpDriver.run(run, Arc::new(Quiet)).await.unwrap();

    let traffic = read_traffic(&log);
    let config: Value = serde_json::from_str(
        recorded_environment(&traffic)["CODEX_CONFIG"]
            .as_str()
            .unwrap(),
    )
    .unwrap();
    assert_eq!(
        config["plugins"]["browser@openai-bundled"]["enabled"],
        json!(false),
        "the pinned adapter's own overrides are kept"
    );
    assert_eq!(config["features"]["hooks"], json!(false));
    assert_eq!(config["web_search"], json!("disabled"));
    // Codex's own prompt carries the instructions, as before.
    assert!(prompts(&traffic)[0].contains("You are Lemma's agent."));
}

#[tokio::test]
async fn opencode_is_started_with_its_own_skills_left_out() {
    let directory = TempDir::new().unwrap();
    let log = directory.path().join("opencode.jsonl");
    let run = request(
        adapter("opencode", &log, BTreeMap::new()),
        directory.path().join("cwd"),
        false,
        Value::Null,
    );
    AcpDriver.run(run, Arc::new(Quiet)).await.unwrap();

    let traffic = read_traffic(&log);
    let environment = recorded_environment(&traffic);
    assert_eq!(environment["OPENCODE_DISABLE_EXTERNAL_SKILLS"], json!("1"));
    assert_eq!(environment["OPENCODE_DISABLE_CLAUDE_CODE"], json!("1"));
    let overlay: Value =
        serde_json::from_str(environment["OPENCODE_CONFIG_CONTENT"].as_str().unwrap()).unwrap();
    assert_eq!(overlay["permission"]["skill"], json!("deny"));

    // And with its own settings, exactly as before.
    let own = directory.path().join("own.jsonl");
    let run = request(
        adapter("opencode", &own, BTreeMap::new()),
        directory.path().join("cwd-own"),
        true,
        Value::Null,
    );
    AcpDriver.run(run, Arc::new(Quiet)).await.unwrap();
    let traffic = read_traffic(&own);
    let environment = recorded_environment(&traffic);
    assert!(
        !environment.keys().any(|name| name.starts_with("OPENCODE_")),
        "{environment:?}"
    );
}

/// Lemma's own `lemma`, when the run names one this Mac accepts, is the one
/// the agent's shell finds first.
#[tokio::test]
async fn lemmas_cli_goes_first_on_the_agents_path() {
    use std::os::unix::fs::PermissionsExt;

    let directory = TempDir::new().unwrap();
    let cli = directory.path().join("pack/backend");
    std::fs::create_dir_all(cli.join("bin")).unwrap();
    std::fs::write(cli.join("bin/lemma"), "#!/bin/sh\n").unwrap();
    std::fs::set_permissions(
        cli.join("bin/lemma"),
        std::fs::Permissions::from_mode(0o755),
    )
    .unwrap();
    let log = directory.path().join("cursor.jsonl");
    let run = request(
        adapter("cursor", &log, BTreeMap::new()),
        directory.path().join("cwd"),
        false,
        json!({ "lemma_cli": cli }),
    );
    AcpDriver.run(run, Arc::new(Quiet)).await.unwrap();

    let traffic = read_traffic(&log);
    let path = recorded_environment(&traffic)["PATH"]
        .as_str()
        .unwrap()
        .to_owned();
    let first = std::env::split_paths(&path).next().unwrap();
    assert_eq!(first, std::fs::canonicalize(&cli).unwrap().join("bin"));
    // Behind it, the adapter's own wiring, which is how the agent is found.
    assert!(std::env::split_paths(&path).count() > 1, "{path}");
}
