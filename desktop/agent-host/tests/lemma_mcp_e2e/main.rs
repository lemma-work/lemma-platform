//! Does an Agent Host run actually reach Lemma's MCP tools?
//!
//! Everything below is hermetic and runs in CI: no provider credentials, no
//! quota, no network beyond loopback. Two layers are covered.
//!
//! 1. The bridge and the relay — `lemma-agent-host mcp-bridge` spawned as the
//!    real subprocess an ACP adapter would spawn, speaking JSON-RPC on stdio to
//!    the host's real relay (`mcp_relay::serve`, run in this process), which
//!    carries each call over a real link to the stand-in control plane.
//! 2. The whole host — the shipped `serve` binary, paired to a stand-in control
//!    plane, dispatching a run to an ACP agent that *does* connect to the MCP
//!    server it is handed and calls a `lemma_*` tool.
//!
//! What this does NOT prove: that Lemma's own MCP tools behave like the
//! stand-in (that is `lemma-backend`'s `test_mcp_client_e2e.py`), nor that a
//! commercial agent chooses to call the tool — see `real_harness_e2e.rs`, whose
//! `#[ignore]`d MCP test drives Codex and Claude Code against this same
//! stand-in.

#![cfg(unix)]

use std::process::Stdio;
use std::time::Duration;

use chrono::Utc;
use lemma_agent_host::config::HostPaths;
use lemma_agent_host::journal::Journal;
use lemma_agent_host::link::{self, LinkSlotOwner};
use lemma_agent_host::protocol::{
    Command, CommandKind, EventType, HostCapacity, HostHello, JsonMap, RunSpec,
};
use serde_json::{Value, json};
use tempfile::TempDir;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use uuid::Uuid;

#[path = "../support/mod.rs"]
mod support;

use support::{
    ControlPlane, HOST_SECRET, HostProcess, LemmaMcpEndpoint, MCP_BEARER, PermissionAnswer,
    ScriptedFailure, ShimmedAgents,
};

mod agents;
mod credentials;
mod transport;

/// Drives `lemma-agent-host mcp-bridge` the way an ACP adapter does.
pub(crate) struct BridgeProcess {
    child: tokio::process::Child,
    stdin: tokio::process::ChildStdin,
    stdout: tokio::io::Lines<BufReader<tokio::process::ChildStdout>>,
    next_id: u64,
}

impl BridgeProcess {
    fn spawn(data_directory: &std::path::Path, target_id: Uuid, run_id: Uuid) -> Self {
        let mut child = tokio::process::Command::new(env!("CARGO_BIN_EXE_lemma-agent-host"))
            .arg("--data-dir")
            .arg(data_directory)
            .arg("mcp-bridge")
            .arg("--target-id")
            .arg(target_id.to_string())
            .arg("--run-id")
            .arg(run_id.to_string())
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::piped())
            .kill_on_drop(true)
            .spawn()
            .expect("the Agent Host binary is built by the test harness");
        let stdin = child.stdin.take().unwrap();
        let stdout = BufReader::new(child.stdout.take().unwrap()).lines();
        Self {
            child,
            stdin,
            stdout,
            next_id: 0,
        }
    }

    async fn notify(&mut self, method: &str) {
        self.write(&json!({"jsonrpc": "2.0", "method": method, "params": {}}))
            .await;
    }

    async fn request(&mut self, method: &str, params: Value) -> Value {
        self.next_id += 1;
        self.write(&json!({
            "jsonrpc": "2.0",
            "id": self.next_id,
            "method": method,
            "params": params,
        }))
        .await;
        let line = tokio::time::timeout(Duration::from_secs(20), self.stdout.next_line())
            .await
            .unwrap_or_else(|_| panic!("the MCP bridge never answered {method}"))
            .unwrap()
            .unwrap_or_else(|| panic!("the MCP bridge closed stdout before answering {method}"));
        serde_json::from_str(&line).expect("the bridge must emit one JSON-RPC message per line")
    }

    /// Writes are best effort: a bridge that has already refused its
    /// configuration closes stdin, and the broken pipe is the point of the
    /// test rather than a harness failure.
    async fn write(&mut self, message: &Value) {
        let _ = self
            .stdin
            .write_all(format!("{message}\n").as_bytes())
            .await;
        let _ = self.stdin.flush().await;
    }

    /// Close stdin the way an adapter does when it tears the session down.
    async fn finish(mut self) -> std::process::Output {
        drop(self.stdin);
        let status = tokio::time::timeout(Duration::from_secs(20), self.child.wait())
            .await
            .expect("the MCP bridge did not exit after its stdin closed")
            .unwrap();
        let mut stderr = String::new();
        if let Some(mut handle) = self.child.stderr.take() {
            use tokio::io::AsyncReadExt;
            let _ = handle.read_to_string(&mut stderr).await;
        }
        std::process::Output {
            status,
            stdout: Vec::new(),
            stderr: stderr.into_bytes(),
        }
    }
}

/// The host's side of the bridge, without the rest of the host: the real
/// relay, fed by a real link to a stand-in control plane that serves
/// `endpoint`.
///
/// The link is kept open the way the worker keeps it open -- reconnected
/// whenever it closes, and published in the slot the relay waits on -- so a
/// test can drop it under a request and watch the relay carry on.
pub(crate) struct Relay {
    /// Lemma's end of the link, for as long as the relay needs one.
    _control: ControlPlane,
    tasks: Vec<tokio::task::JoinHandle<()>>,
}

impl Drop for Relay {
    fn drop(&mut self) {
        for task in &self.tasks {
            task.abort();
        }
    }
}

pub(crate) async fn start_relay(
    paths: &HostPaths,
    target_id: Uuid,
    endpoint: &LemmaMcpEndpoint,
) -> Relay {
    // A control plane with no harness published offers no run; it is here
    // for its end of the link.
    let control =
        ControlPlane::start("cursor", "unused", json!({}), PermissionAnswer::Ignore).await;
    control.serve_mcp(endpoint);
    let (owner, slot) = LinkSlotOwner::new();
    let base_url = control.base_url.clone();
    let link = tokio::spawn(async move {
        loop {
            match link::connect(
                &base_url,
                HOST_SECRET,
                HostHello::current("relay-e2e"),
                HostCapacity::default(),
            )
            .await
            {
                Ok(connected) => {
                    owner.set(Some(connected.handle.clone()));
                    connected.handle.closed().await;
                    owner.set(None);
                }
                Err(_) => tokio::time::sleep(Duration::from_millis(50)).await,
            }
        }
    });
    let relay = lemma_agent_host::mcp_relay::serve(
        paths,
        target_id,
        Journal::open(&paths.journal).unwrap(),
        slot,
    )
    .expect("the relay binds a loopback port");
    Relay {
        _control: control,
        tasks: vec![link, tokio::spawn(relay)],
    }
}

/// A journalled run, a relay for it, and the bridge an adapter would spawn.
pub(crate) async fn bridge_for(
    directory: &TempDir,
    endpoint: &LemmaMcpEndpoint,
    mcp: Value,
) -> (BridgeProcess, Relay, Uuid, Uuid) {
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    let target_id = Uuid::new_v4();
    let run_id = Uuid::new_v4();
    journal_run(&paths, target_id, run_id, mcp);
    let relay = start_relay(&paths, target_id, endpoint).await;
    let bridge = BridgeProcess::spawn(directory.path(), target_id, run_id);
    (bridge, relay, target_id, run_id)
}

/// Journal a run the way `handle_start` does, so the relay can find its
/// credential. The relay reads the run spec from the journal on every call
/// rather than the bridge taking it on its argv, which is the whole reason the
/// credential never reaches a command line or an environment variable.
pub(crate) fn journal_run(paths: &HostPaths, target_id: Uuid, run_id: Uuid, mcp: Value) {
    let journal = Journal::open(&paths.journal).unwrap();
    let spec = RunSpec {
        agent_run_id: run_id,
        conversation_id: Uuid::new_v4(),
        harness_id: Uuid::new_v4(),
        profile_revision: "bridge-e2e".to_owned(),
        model_name: None,
        config_selections: JsonMap::new(),
        system_prompt: String::new(),
        prompt: vec![json!({"type": "text", "text": "unused"})],
        resume_session_id: None,
        workspace_cwd: None,
        context: JsonMap::new(),
        mcp,
        run_deadline: Utc::now() + chrono::Duration::minutes(5),
        system_prompt_delivery: None,
    };
    let command = Command {
        command_id: Uuid::new_v4(),
        kind: CommandKind::StartRun,
        created_at: Utc::now(),
        expires_at: Utc::now() + chrono::Duration::minutes(5),
        run_id: Some(run_id),
        lease_epoch: Some(1),
        payload: serde_json::to_value(&spec).unwrap(),
    };
    journal
        .accept_start(target_id, &command, &spec, "cursor", "native-acp-1")
        .unwrap();
}

/// A full MCP client conversation over the bridge: handshake, discovery, call.
pub(crate) async fn drive_bridge(bridge: &mut BridgeProcess) -> (Value, Value) {
    let initialized = bridge
        .request(
            "initialize",
            json!({
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "bridge-e2e", "version": "1.0.0"},
            }),
        )
        .await;
    // Answered by the bridge itself: the handshake says nothing Lemma needs
    // to hear, so it no longer costs a round trip.
    assert_eq!(initialized["result"]["serverInfo"]["name"], "lemma");
    bridge.notify("notifications/initialized").await;
    let listed = bridge.request("tools/list", json!({})).await;
    let called = bridge
        .request(
            "tools/call",
            json!({
                "name": "lemma_echo",
                "arguments": {"text": "BRIDGE_ROUND_TRIP"},
            }),
        )
        .await;
    (listed, called)
}
