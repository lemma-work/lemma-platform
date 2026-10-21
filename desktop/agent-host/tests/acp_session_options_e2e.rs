//! What each coding agent is started with beyond ACP, as the agent sees it.
//!
//! The real driver against `fake_acp_agent.py`, which records every message it
//! is sent and the environment it was started in. The switches themselves are
//! verified against each adapter's source in `acp::session_options`; this holds
//! the host to delivering them: Claude Code's in the session's `_meta` (on a
//! new session and a resumed one alike), and nothing of the kind when the
//! person chose Claude Code's own skills and settings. Codex and `OpenCode`
//! get no personal-setup switches at all, only Lemma's web-tool overrides
//! (and, for Codex, Lemma's server in its config).
//! Every agent still has Lemma's MCP server, sign-in and instructions.

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;
use std::time::Duration;

use agent_client_protocol::schema::v1::{McpServer, McpServerStdio};
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
        lemma_cli_bin: None,
        steer: lemma_agent_host::acp::SteerInbox::default(),
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
    AcpDriver::default()
        .run(first.clone(), Arc::new(Quiet))
        .await
        .unwrap();
    let mut resumed = first;
    resumed.run_spec.resume_session_id = Some("fake-session".into());
    AcpDriver::default()
        .run(resumed, Arc::new(Quiet))
        .await
        .unwrap();

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
    AcpDriver::default()
        .run(run, Arc::new(Quiet))
        .await
        .unwrap();

    let traffic = read_traffic(&log);
    let options = &sessions(&traffic)[0]["params"]["_meta"]["claudeCode"]["options"];
    assert!(options.get("settingSources").is_none(), "{options}");
    assert!(options.get("strictMcpConfig").is_none(), "{options}");
    // Lemma's tools are in the prompt either way, not behind a search.
    assert_eq!(options["env"]["ENABLE_TOOL_SEARCH"], json!("false"));
    // Lemma's browser is the one the person watches, whatever the setting.
    assert!(
        options["disallowedTools"]
            .as_array()
            .unwrap()
            .contains(&json!("mcp__claude-in-chrome"))
    );
}

#[tokio::test]
async fn codex_keeps_the_persons_setup_and_the_pinned_config() {
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
    AcpDriver::default()
        .run(run, Arc::new(Quiet))
        .await
        .unwrap();

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
    // Nothing of the person's own setup is switched off.
    assert!(config.get("features").is_none(), "{config}");
    assert!(config.get("skills").is_none(), "{config}");
    assert_eq!(config["web_search"], json!("disabled"));
    // Codex's own prompt carries the instructions, as before.
    assert!(prompts(&traffic)[0].contains("You are Lemma's agent."));
}

/// `OpenCode` loads the person's own skills and config whichever way the
/// own-settings switch is set: only Claude Code reads it.
#[tokio::test]
async fn opencode_keeps_the_persons_own_setup_either_way() {
    let directory = TempDir::new().unwrap();
    for own in [false, true] {
        let log = directory.path().join(format!("opencode-{own}.jsonl"));
        let run = request(
            adapter("opencode", &log, BTreeMap::new()),
            directory.path().join(format!("cwd-{own}")),
            own,
            Value::Null,
        );
        AcpDriver::default()
            .run(run, Arc::new(Quiet))
            .await
            .unwrap();
        let traffic = read_traffic(&log);
        let environment = recorded_environment(&traffic);
        assert!(
            !environment.keys().any(|name| name.starts_with("OPENCODE_")),
            "own_settings={own}: {environment:?}"
        );
        assert!(prompts(&traffic)[0].contains("You are Lemma's agent."));
    }
}

/// A folder holding an executable `bin/lemma`, as a CLI root is.
#[cfg(unix)]
fn cli_folder(at: &Path) -> PathBuf {
    use std::os::unix::fs::PermissionsExt;

    std::fs::create_dir_all(at.join("bin")).unwrap();
    std::fs::write(at.join("bin/lemma"), "#!/bin/sh\n").unwrap();
    std::fs::set_permissions(at.join("bin/lemma"), std::fs::Permissions::from_mode(0o755)).unwrap();
    std::fs::canonicalize(at).unwrap()
}

/// A Mac with the CLI a server names (a host pack's) and the one shipped in
/// the app, and a home folder holding neither.
#[cfg(unix)]
struct Clis {
    directory: TempDir,
    home: PathBuf,
    named: PathBuf,
    bundled: PathBuf,
}

#[cfg(unix)]
fn clis() -> Clis {
    let directory = TempDir::new().unwrap();
    let home = directory.path().join("home");
    std::fs::create_dir_all(&home).unwrap();
    Clis {
        home: std::fs::canonicalize(&home).unwrap(),
        named: cli_folder(&directory.path().join("pack/backend")),
        bundled: cli_folder(
            &directory
                .path()
                .join("Lemma.app/Contents/Resources/lemma-cli"),
        ),
        directory,
    }
}

/// The run as the runtime starts it: the server's word on the CLI in `mcp`,
/// and the `bin/` the host chose for this pairing from it and the app.
#[cfg(unix)]
fn run_choosing(key: &str, log: &Path, clis: &Clis, local: bool, bundled: &Path) -> AcpRunRequest {
    let mut run = request(
        adapter(key, log, BTreeMap::new()),
        clis.directory.path().join(format!("cwd-{key}-{local}")),
        false,
        json!({ "lemma_cli": clis.named }),
    );
    run.lemma_cli_bin = lemma_agent_host::lemma_cli::cli_root(
        local,
        run.run_spec.mcp["lemma_cli"].as_str(),
        &[bundled.to_path_buf()],
        &clis.home,
    )
    .map(|root| root.join("bin"));
    run
}

#[cfg(unix)]
fn allowed_tools(traffic: &[Value]) -> Value {
    sessions(traffic)[0]["params"]["_meta"]["claudeCode"]["options"]
        .get("allowedTools")
        .cloned()
        .unwrap_or(Value::Null)
}

#[cfg(unix)]
fn first_on_path(traffic: &[Value]) -> PathBuf {
    let path = recorded_environment(traffic)["PATH"]
        .as_str()
        .unwrap()
        .to_owned();
    // Behind it, the adapter's own wiring, which is how the agent is found.
    assert!(std::env::split_paths(&path).count() > 1, "{path}");
    std::env::split_paths(&path).next().unwrap()
}

/// Lemma's own `lemma`, when the run names one this Mac accepts, is the one
/// the agent's shell finds first.
#[cfg(unix)]
#[tokio::test]
async fn lemmas_cli_goes_first_on_the_agents_path() {
    let clis = clis();
    let log = clis.directory.path().join("cursor.jsonl");
    let run = run_choosing("cursor", &log, &clis, true, &clis.bundled);
    AcpDriver::default()
        .run(run, Arc::new(Quiet))
        .await
        .unwrap();

    assert_eq!(first_on_path(&read_traffic(&log)), clis.named.join("bin"));
}

/// The run's own `lemma` raises no permission request: Claude Code is given
/// the binary by its full path and by the name its `PATH` resolves to it. On
/// the local install's pairing that is the CLI its server names.
#[cfg(unix)]
#[tokio::test]
async fn claude_code_may_run_lemmas_cli_without_asking() {
    let clis = clis();
    let log = clis.directory.path().join("claude.jsonl");
    let run = run_choosing("claude-code", &log, &clis, true, &clis.bundled);
    AcpDriver::default()
        .run(run, Arc::new(Quiet))
        .await
        .unwrap();

    let traffic = read_traffic(&log);
    let binary = clis.named.join("bin/lemma");
    assert_eq!(
        allowed_tools(&traffic),
        json!([format!("Bash({}:*)", binary.display()), "Bash(lemma:*)"])
    );
    assert_eq!(first_on_path(&traffic).join("lemma"), binary);
}

/// A hosted workspace's run gets the CLI shipped in the app, never one its
/// server names: that is a path on the server's machine, and which binary on
/// this Mac runs unasked is not a remote server's to choose.
#[cfg(unix)]
#[tokio::test]
async fn a_hosted_run_may_run_the_cli_bundled_with_the_app() {
    let clis = clis();
    let log = clis.directory.path().join("claude-hosted.jsonl");
    let run = run_choosing("claude-code", &log, &clis, false, &clis.bundled);
    AcpDriver::default()
        .run(run, Arc::new(Quiet))
        .await
        .unwrap();

    let traffic = read_traffic(&log);
    let binary = clis.bundled.join("bin/lemma");
    assert_eq!(
        allowed_tools(&traffic),
        json!([format!("Bash({}:*)", binary.display()), "Bash(lemma:*)"])
    );
    assert_eq!(first_on_path(&traffic).join("lemma"), binary);
}

/// An app with no CLI in it gives a hosted run no allow rule at all, rather
/// than one naming whatever its server said.
#[cfg(unix)]
#[tokio::test]
async fn a_hosted_run_without_a_bundled_cli_is_allowed_none() {
    let clis = clis();
    let log = clis.directory.path().join("claude-unbundled.jsonl");
    let missing = clis
        .directory
        .path()
        .join("Other.app/Contents/Resources/lemma-cli");
    let run = run_choosing("claude-code", &log, &clis, false, &missing);
    assert!(run.lemma_cli_bin.is_none());
    AcpDriver::default()
        .run(run, Arc::new(Quiet))
        .await
        .unwrap();

    let traffic = read_traffic(&log);
    assert_eq!(allowed_tools(&traffic), Value::Null);
    let path = recorded_environment(&traffic)
        .get("PATH")
        .and_then(Value::as_str)
        .unwrap_or_default()
        .to_owned();
    assert!(
        !std::env::split_paths(&path).any(|entry| entry == clis.named.join("bin")),
        "{path}"
    );
}

/// Serves one CLI archive (a gzipped tar of `bin/lemma`) and its manifest, as
/// a GitHub release does, and returns the manifest's URL.
#[cfg(unix)]
async fn serve_cli_release(directory: &Path) -> String {
    use axum::{Router, routing::get};
    use sha2::Digest as _;

    let tree = cli_folder(&directory.join("release-tree"));
    let packed = directory.join("lemma-cli.tar.gz");
    let mut tar = std::process::Command::new("tar");
    tar.arg("-czf").arg(&packed).arg("-C").arg(&tree).arg("bin");
    assert!(tar.status().unwrap().success());
    let archive = std::fs::read(&packed).unwrap();
    std::fs::remove_file(&packed).unwrap();
    let listener = tokio::net::TcpListener::bind("127.0.0.1:0").await.unwrap();
    let address = listener.local_addr().unwrap();
    let manifest = json!({
        "version": "9.8.7",
        "platform": "aarch64-apple-darwin",
        "url": format!("http://{address}/v/lemma-cli.tar.gz"),
        "sha256": hex::encode(sha2::Sha256::digest(&archive)),
        "size": archive.len(),
    })
    .to_string();
    let app = Router::new()
        .route("/v/lemma-cli.json", get(move || async move { manifest }))
        .route("/v/lemma-cli.tar.gz", get(move || async move { archive }));
    tokio::spawn(async move { axum::serve(listener, app).await.unwrap() });
    format!("http://{address}/v/lemma-cli.json")
}

/// An app with no CLI in it, on a hosted pairing: the host downloads its own
/// release's CLI, and the run's agent may run exactly that binary.
#[cfg(unix)]
#[tokio::test(flavor = "multi_thread")]
async fn a_hosted_run_without_a_bundled_cli_may_run_the_downloaded_one() {
    use lemma_agent_host::lemma_cli::download::Downloader;

    let clis = clis();
    let data = clis.directory.path().join("agent-host-data");
    let manifest = Some(serve_cli_release(clis.directory.path()).await);
    let fetcher = Downloader::for_release(&data, manifest, "9.8.7", "aarch64-apple-darwin");
    let downloaded = Arc::new(fetcher)
        .ready_within(Duration::from_secs(60))
        .await;
    let downloaded = std::fs::canonicalize(downloaded.expect("the CLI downloads")).unwrap();

    let log = clis.directory.path().join("claude-downloaded.jsonl");
    let missing = clis
        .directory
        .path()
        .join("Other.app/Contents/Resources/lemma-cli");
    let mut run = run_choosing("claude-code", &log, &clis, false, &missing);
    // As the runtime resolves it: bundled first, then downloaded.
    let candidates = [missing, downloaded.clone()];
    let named = run.run_spec.mcp["lemma_cli"].as_str();
    run.lemma_cli_bin =
        lemma_agent_host::lemma_cli::cli_root(false, named, &candidates, &clis.home)
            .map(|root| root.join("bin"));
    AcpDriver::default()
        .run(run, Arc::new(Quiet))
        .await
        .unwrap();

    let traffic = read_traffic(&log);
    let binary = downloaded.join("bin/lemma");
    assert!(binary.starts_with(std::fs::canonicalize(&data).unwrap()));
    let rules = json!([format!("Bash({}:*)", binary.display()), "Bash(lemma:*)"]);
    assert_eq!(allowed_tools(&traffic), rules);
    assert_eq!(first_on_path(&traffic).join("lemma"), binary);
}

/// What each agent's run is given when the person has not chosen their own
/// setup: Lemma's MCP server, Lemma's sign-in and Lemma's instructions. Codex
/// and `OpenCode` stay in the person's own config folders, as they are.
#[tokio::test]
async fn each_agent_keeps_lemmas_server_sign_in_and_instructions() {
    let directory = TempDir::new().unwrap();
    let lemma = BTreeMap::from([
        ("LEMMA_TOKEN".to_owned(), "run-token".to_owned()),
        ("LEMMA_BASE_URL".to_owned(), "http://127.0.0.1:1".to_owned()),
    ]);

    for agent in ["codex", "claude-code", "opencode"] {
        let log = directory.path().join(format!("{agent}.jsonl"));
        let mut run = request(
            adapter(agent, &log, BTreeMap::new()),
            directory.path().join(format!("cwd-{agent}")),
            false,
            Value::Null,
        );
        run.agent_environment.clone_from(&lemma);
        run.mcp_server = Some(McpServer::Stdio(McpServerStdio::new(
            "lemma",
            PathBuf::from("lemma-agent-host"),
        )));
        AcpDriver::default()
            .run(run, Arc::new(Quiet))
            .await
            .unwrap();

        let traffic = read_traffic(&log);
        let environment = recorded_environment(&traffic);
        assert_eq!(environment["LEMMA_TOKEN"], json!("run-token"), "{agent}");
        assert_eq!(
            environment["LEMMA_BASE_URL"],
            json!("http://127.0.0.1:1"),
            "{agent}"
        );
        let session = sessions(&traffic)[0];
        let servers = session["params"]["mcpServers"].as_array().unwrap();
        if agent == "codex" {
            // Codex is given the server in its config, where it can carry
            // the bridge's call limit, and only there.
            let config: Value =
                serde_json::from_str(environment["CODEX_CONFIG"].as_str().unwrap()).unwrap();
            assert_eq!(
                config["mcp_servers"]["lemma"]["tool_timeout_sec"],
                json!(1800),
                "{config}"
            );
            assert_eq!(
                config["mcp_servers"]["lemma"]["command"],
                json!("lemma-agent-host")
            );
            assert!(servers.is_empty(), "{session}");
        } else {
            assert!(
                servers.iter().any(|server| server["name"] == "lemma"),
                "{agent}: {session}"
            );
        }

        if agent == "claude-code" {
            let meta = &session["params"]["_meta"];
            assert_eq!(
                meta["systemPrompt"],
                json!({ "append": "You are Lemma's agent." })
            );
            let options = &meta["claudeCode"]["options"];
            assert_eq!(options["settingSources"], json!(["project", "local"]));
            assert_eq!(options["strictMcpConfig"], json!(true));
            assert_eq!(options["plugins"], json!([]));
        } else {
            // The person's own config folder, with Lemma's instructions on top.
            for folder in ["CODEX_HOME", "XDG_CONFIG_HOME"] {
                assert_eq!(
                    environment.get(folder).and_then(Value::as_str),
                    std::env::var(folder).ok().as_deref(),
                    "{agent}: {folder} is the host's own"
                );
            }
            assert!(
                prompts(&traffic)[0].contains("You are Lemma's agent."),
                "{agent}: {traffic:?}"
            );
            // And none of the switches that would hide the person's setup:
            // Codex's config holds Lemma's server and nothing else.
            assert!(
                !environment.keys().any(|name| name.starts_with("OPENCODE_")),
                "{agent}: {environment:?}"
            );
            if let Some(config) = environment.get("CODEX_CONFIG") {
                let config: Value = serde_json::from_str(config.as_str().unwrap()).unwrap();
                let keys: Vec<&String> = config.as_object().unwrap().keys().collect();
                assert_eq!(keys, ["mcp_servers"], "{agent}: {config}");
            }
        }
    }
}
