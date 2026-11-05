//! Which `lemma` each harness's agent runs, as the agent sees it.
//!
//! The real driver against `fake_acp_agent.py`, which records its environment
//! and asks the shells a harness runs commands in (`zsh -c`, `zsh -lc`,
//! `bash -lc`) for `command -v lemma`. Its home folder has startup files that
//! put a person's own, globally installed `lemma` first, as `~/.local/bin`
//! usually does. Live QA found Codex and `OpenCode` running that copy rather than
//! the one Lemma chose; only Claude Code was covered. Every harness is now.
#![cfg(unix)]

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

const HARNESSES: [&str; 4] = ["claude-code", "codex", "opencode", "cursor"];

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
        .flat_map(|path| [path.join("python3"), path.join("python")])
        .find(|candidate| candidate.is_file())
        .expect("Python is required for the ACP process integration test")
}

fn executable(path: &Path) {
    use std::os::unix::fs::PermissionsExt;
    std::fs::create_dir_all(path.parent().unwrap()).unwrap();
    std::fs::write(path, "#!/bin/sh\n").unwrap();
    std::fs::set_permissions(path, std::fs::Permissions::from_mode(0o755)).unwrap();
}

struct Machine {
    directory: TempDir,
    home: PathBuf,
    /// The person's own `lemma`, which their startup files put first.
    theirs: PathBuf,
    /// The CLI Lemma chose for the run.
    bin: PathBuf,
}

fn machine() -> Machine {
    let directory = TempDir::new().unwrap();
    let base = std::fs::canonicalize(directory.path()).unwrap();
    let home = base.join("home");
    let theirs = home.join(".local/bin/lemma");
    executable(&theirs);
    for file in [".zshenv", ".zprofile", ".bash_profile", ".bashrc"] {
        std::fs::write(home.join(file), "export PATH=\"$HOME/.local/bin:$PATH\"\n").unwrap();
    }
    let bin = base.join("lemma-cli/bin");
    executable(&bin.join("lemma"));
    Machine {
        directory,
        home,
        theirs,
        bin,
    }
}

fn request(key: &str, mac: &Machine, log: &Path, lemma_cli_bin: Option<PathBuf>) -> AcpRunRequest {
    let fixture = Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/fake_acp_agent.py");
    let environment = BTreeMap::from([
        ("HOME".to_owned(), mac.home.display().to_string()),
        ("FAKE_ACP_PROBE_LEMMA".to_owned(), "1".to_owned()),
    ]);
    let adapter = ResolvedAdapter {
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
    };
    let lemma = json!({
        "LEMMA_TOKEN": "run-token",
        "LEMMA_BASE_URL": "https://lemma.example",
    });
    AcpRunRequest {
        adapter,
        agent_environment: BTreeMap::default(),
        own_settings: false,
        lemma_cli_bin,
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
            mcp: json!({ "environment": lemma }),
            run_deadline: chrono::Utc::now() + chrono::Duration::minutes(1),
            system_prompt_delivery: None,
            pre_approvals: Vec::new(),
        },
        scratch_directory: mac.directory.path().join(format!("cwd-{key}")),
        mcp_server: None,
        can_load_session: true,
        published_config_options: Vec::new(),
        permissions: PermissionGate::new(),
        permission_timeout: Duration::ZERO,
        cancel: lemma_agent_host::acp::never_cancelled(),
        cancel_grace: Duration::from_secs(5),
    }
}

/// The agent's recorded environment, what its shells found as `lemma`, and
/// the adapter's own `PATH` -- how a real adapter reaches its agent.
async fn run(key: &str, mac: &Machine, bin: Option<PathBuf>) -> (Value, Value, String) {
    let log = mac
        .directory
        .path()
        .join(format!("{key}-{}.jsonl", bin.is_some()));
    let mut request = request(key, mac, &log, bin);
    let adapters_own = request.adapter.environment()["PATH"].clone();
    // Lemma's sign-in, as the runtime puts it in the agent's environment.
    request.agent_environment = BTreeMap::from([
        ("LEMMA_TOKEN".to_owned(), "run-token".to_owned()),
        (
            "LEMMA_BASE_URL".to_owned(),
            "https://lemma.example".to_owned(),
        ),
    ]);
    AcpDriver::default()
        .run(request, Arc::new(Quiet))
        .await
        .unwrap();
    let traffic: Vec<Value> = std::fs::read_to_string(&log)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str(line).unwrap())
        .collect();
    let find = |name: &str| {
        traffic
            .iter()
            .find_map(|line| line.get(name).cloned())
            .unwrap_or_else(|| panic!("the agent recorded no {name}"))
    };
    (find("environment"), find("shell_lemma"), adapters_own)
}

fn first_on_path(environment: &Value) -> PathBuf {
    std::env::split_paths(environment["PATH"].as_str().unwrap())
        .next()
        .unwrap()
}

/// Whichever harness runs, the CLI Lemma chose is first on the agent's
/// `PATH`, and first again in every shell it runs commands in, after the
/// person's startup files have had their say. Lemma's sign-in comes too.
#[tokio::test(flavor = "multi_thread")]
async fn every_harness_runs_the_cli_lemma_chose() {
    let mac = machine();
    let chosen = mac.bin.join("lemma").display().to_string();
    for key in HARNESSES {
        let (environment, shells, adapters_own) = run(key, &mac, Some(mac.bin.clone())).await;
        assert_eq!(first_on_path(&environment), mac.bin, "{key}");
        // Behind it, the adapter's own wiring, untouched.
        assert_eq!(
            environment["PATH"],
            json!(format!("{}:{adapters_own}", mac.bin.display())),
            "{key}"
        );
        assert_eq!(environment["LEMMA_TOKEN"], "run-token", "{key}");
        assert_eq!(
            environment["LEMMA_BASE_URL"], "https://lemma.example",
            "{key}"
        );
        assert_eq!(
            environment["LEMMAHOST_CLI_BIN"],
            json!(mac.bin.display().to_string())
        );
        let shells = shells.as_object().unwrap();
        assert!(!shells.is_empty(), "{key}: no shell to ask");
        for (shell, found) in shells {
            assert_eq!(found, &json!(chosen), "{key}: {shell}");
        }
    }
}

/// With no CLI chosen, nothing about the agent's `PATH` or shells changes:
/// the person's own `lemma`, if any, is what their shells find, as before.
#[tokio::test(flavor = "multi_thread")]
async fn with_no_cli_chosen_the_path_is_the_adapters_own() {
    let mac = machine();
    let theirs = mac.theirs.display().to_string();
    for key in HARNESSES {
        let (environment, shells, adapters_own) = run(key, &mac, None).await;
        assert_eq!(environment["PATH"], json!(adapters_own), "{key}");
        for absent in ["ZDOTDIR", "BASH_ENV", "LEMMAHOST_CLI_BIN"] {
            assert!(environment.get(absent).is_none(), "{key}: {absent}");
        }
        for (shell, found) in shells.as_object().unwrap() {
            assert_eq!(found, &json!(theirs), "{key}: {shell}");
        }
    }
}
