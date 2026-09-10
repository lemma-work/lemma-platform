//! Opt-in tests against authenticated local ACP harnesses using dedicated test accounts.
//!
//! These are ignored in CI because they spend real provider quota and depend on
//! local Codex, Claude Code, and `OpenCode` credentials.

use std::collections::BTreeMap;
use std::path::PathBuf;
use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use chrono::Utc;

use lemma_agent_host::acp::{AcpCallbacks, AcpDriver, AcpRunOutcome, AcpRunRequest, AgentDriver};
use lemma_agent_host::adapters::AdapterManifest;
use lemma_agent_host::config::HostPaths;
use lemma_agent_host::permissions::PermissionGate;
use lemma_agent_host::protocol::{EventType, JsonMap, RunSpec, RunState};
use serde_json::{Value, json};
use tempfile::TempDir;
use uuid::Uuid;

#[path = "../support/mod.rs"]
mod support;

mod cancellation;
mod streaming;
mod tools;
mod workspace;

#[derive(Default)]
pub(crate) struct StreamCapture {
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

pub(crate) async fn run_with_deadline(
    request: AcpRunRequest,
    callbacks: Arc<dyn AcpCallbacks>,
) -> anyhow::Result<AcpRunOutcome> {
    let remaining = (request.run_spec.run_deadline - Utc::now())
        .to_std()
        .unwrap_or_default();
    tokio::time::timeout(remaining, AcpDriver.run(request, callbacks))
        .await
        .map_err(|_| anyhow::anyhow!("live ACP test exceeded its run deadline"))?
}

pub(crate) fn configured_agents() -> Vec<String> {
    parse_agents(
        &std::env::var("LEMMA_REAL_AGENT_E2E_AGENTS")
            .unwrap_or_else(|_| "codex,claude-code,opencode".to_owned()),
    )
    .unwrap_or_else(|error| panic!("invalid live agent selection: {error}"))
}

pub(crate) fn parse_agents(selection: &str) -> anyhow::Result<Vec<String>> {
    let agents: Vec<String> = selection
        .split(',')
        .map(str::trim)
        .filter(|agent| !agent.is_empty())
        .map(str::to_owned)
        .collect();
    anyhow::ensure!(
        !agents.is_empty(),
        "select at least one live agent to qualify"
    );
    let manifest = AdapterManifest::builtin()?;
    let valid: Vec<&str> = manifest
        .adapters
        .iter()
        .map(|adapter| adapter.key.as_str())
        .collect();
    for (index, agent) in agents.iter().enumerate() {
        anyhow::ensure!(
            valid.contains(&agent.as_str()),
            "unknown agent {agent:?}; choose from {}",
            valid.join(", ")
        );
        anyhow::ensure!(
            !agents[..index].contains(agent),
            "agent {agent:?} was selected more than once"
        );
    }
    Ok(agents)
}

pub(crate) fn agent_host_data_directory() -> PathBuf {
    std::env::var_os("LEMMA_REAL_AGENT_HOST_DATA_DIR")
        .or_else(|| std::env::var_os("LEMMA_AGENT_HOST_DATA_DIR"))
        .map(PathBuf::from)
        .expect(
            "set LEMMA_REAL_AGENT_HOST_DATA_DIR to an Agent Host directory whose pinned \
             adapters have been installed with `lemma-agent-host doctor --repair`",
        )
}

/// The one working directory a conversation's turns all share.
///
/// A provider may refuse to load a session into a different cwd — Claude Code
/// does — so a conversation that wandered between directories would fail every
/// resume and start over each turn. `runtime::scratch_directory` keys the real
/// thing on the conversation for the same reason.
pub(crate) fn conversation_workspace(
    paths: &HostPaths,
    agent: &str,
    conversation_id: Uuid,
) -> PathBuf {
    paths
        .root
        .join("real-conversations")
        .join(agent)
        .join(conversation_id.to_string())
}

/// Drive one turn and return `(session id, what the agent streamed back)`.
pub(crate) async fn one_turn(
    manifest: &AdapterManifest,
    workspace: &std::path::Path,
    agent: &str,
    conversation_id: Uuid,
    prompt: &str,
    resume_session_id: Option<String>,
) -> (String, String) {
    let callbacks = Arc::new(StreamCapture::default());
    let run_id = Uuid::new_v4();
    let outcome = run_with_deadline(
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
                workspace_cwd: None,
                context: BTreeMap::new(),
                mcp: Value::Null,
                run_deadline: Utc::now() + chrono::Duration::minutes(5),
                system_prompt_delivery: None,
            },
            // The caller's directory, not one invented here. A provider
            // is entitled to refuse to load a session into a different cwd
            // — Claude Code does — and a Lemma conversation keeps one
            // workspace precisely so this cannot drift.
            scratch_directory: workspace.to_path_buf(),
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

/// Runs one real agent through a paired Agent Host wired to `control`.
///
/// This is the same in-process `HostRuntime` the paired smoke test uses, but
/// pointed at `support::ControlPlane` so the run's MCP endpoint and its
/// permission decisions can be scripted.
pub(crate) async fn paired_real_run(
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
    assert_replay_matches_live(agent, &control);
    (directory, control)
}

pub(crate) fn assert_replay_matches_live(agent: &str, control: &support::ControlPlane) {
    let durable_text = control
        .events()
        .iter()
        .filter(|event| event.event_type == EventType::AgentMessageUpsert)
        .filter_map(|event| event.payload.get("text").and_then(Value::as_str))
        .collect::<String>();
    assert_eq!(
        durable_text,
        control.assistant_text(),
        "{agent}: durable replay differs from the live answer"
    );
}

pub(crate) async fn run_through_paired_agent_host(source_paths: &HostPaths, agent: &str) {
    let directory = TempDir::new().unwrap();
    let mcp = support::LemmaMcpEndpoint::start(support::McpTransport::StatelessJson).await;
    let control = support::ControlPlane::start(
        agent,
        "Begin your reply with LEMMA_PAIRED_AGENT_HOST_STREAM_OK, then write a detailed \
         1000-word essay about the history of the bicycle directly in your reply. \
         Do not use tools. End with LEMMA_STREAM_COMPLETE.",
        mcp.run_configuration(),
        support::PermissionAnswer::Deny,
    )
    .await;
    let host = support::InProcessHost::start(
        directory.path(),
        &control,
        &source_paths.adapters,
        PathBuf::from(env!("CARGO_BIN_EXE_lemma-agent-host")),
    )
    .await;
    let mut saw_live_text = false;
    control
        .wait_for("live assistant text", Duration::from_secs(150), |control| {
            assert!(!host.is_finished(), "{agent}: host exited during streaming");
            control
                .assistant_text()
                .contains("LEMMA_PAIRED_AGENT_HOST_STREAM_OK")
                || control.saw_terminal()
        })
        .await;
    let live_events = control.events();
    if live_events
        .iter()
        .any(|event| event.event_type == EventType::AgentMessageChunk)
        && !live_events
            .iter()
            .any(|event| event.event_type == EventType::Terminal)
    {
        saw_live_text = true;
    }
    control
        .wait_for(
            "stream completion",
            Duration::from_secs(180),
            support::ControlPlane::saw_terminal,
        )
        .await;
    host.shutdown().await;
    let events = control.events();
    assert_replay_matches_live(agent, &control);
    assert!(
        control.rejections().is_empty(),
        "{agent}: host rejected the run"
    );
    let terminals = events
        .iter()
        .filter(|event| event.event_type == EventType::Terminal)
        .collect::<Vec<_>>();
    assert_eq!(
        terminals.len(),
        1,
        "{agent}: expected exactly one terminal event"
    );
    assert_eq!(
        terminals[0].payload["state"], "SUCCEEDED",
        "{agent}: {:?}",
        terminals[0].payload
    );
    assert!(
        saw_live_text,
        "{agent}: no text reached the control plane before completion"
    );
    assert!(
        control.assistant_text().contains("LEMMA_STREAM_COMPLETE"),
        "{agent}: stream was truncated"
    );
    assert!(
        events
            .windows(2)
            .all(|pair| pair[1].sequence == pair[0].sequence + 1),
        "{agent}: event sequence contains a gap or duplicate"
    );
    println!(
        "{agent}: paired Agent Host delivered text before completion and a complete final answer"
    );
}
