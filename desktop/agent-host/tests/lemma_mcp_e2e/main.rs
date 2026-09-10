//! Does an Agent Host run actually reach Lemma's MCP tools?
//!
//! Everything below is hermetic and runs in CI: no provider credentials, no
//! quota, no network beyond loopback. Two layers are covered.
//!
//! 1. The bridge on its own — `lemma-agent-host mcp-bridge` spawned as the real
//!    subprocess an ACP adapter would spawn, speaking JSON-RPC on stdio while a
//!    stand-in for `/agent-runtime/conversations/{id}/mcp` answers over HTTP.
//! 2. The whole host — the shipped `serve` binary, paired to a stand-in control
//!    plane, dispatching a run to an ACP agent that *does* connect to the MCP
//!    server it is handed and calls a `lemma_*` tool.
//!
//! What this does NOT prove: that Lemma's own MCP endpoint behaves like the
//! stand-in (that is `lemma-backend`'s `test_mcp_client_e2e.py`), nor that a
//! commercial agent chooses to call the tool — see `real_harness_e2e.rs`, whose
//! `#[ignore]`d MCP test drives Codex and Claude Code against this same
//! stand-in endpoint.

#![cfg(unix)]

use std::process::Stdio;
use std::time::Duration;

use chrono::Utc;
use lemma_agent_host::config::HostPaths;
use lemma_agent_host::journal::Journal;
use lemma_agent_host::protocol::{Command, CommandKind, EventType, JsonMap, RunSpec};
use serde_json::{Value, json};
use tempfile::TempDir;
use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};
use uuid::Uuid;

#[path = "../support/mod.rs"]
mod support;

use support::{
    ControlPlane, HostProcess, LemmaMcpEndpoint, McpTransport, PermissionAnswer, ScriptedFailure,
    ShimmedAgents,
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

/// Journal a run the way `handle_start` does, so the bridge can find its
/// configuration. The bridge reads the run spec from the journal rather than
/// from its argv, which is the whole reason the credential never reaches a
/// command line or an environment variable.
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
    assert_eq!(initialized["result"]["serverInfo"]["name"], "lemma_tools");
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
