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

mod support;

use support::{
    ControlPlane, HostProcess, LemmaMcpEndpoint, McpTransport, PermissionAnswer, ShimmedAgents,
};

/// Drives `lemma-agent-host mcp-bridge` the way an ACP adapter does.
struct BridgeProcess {
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
fn journal_run(paths: &HostPaths, target_id: Uuid, run_id: Uuid, mcp: Value) {
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
        context: JsonMap::new(),
        mcp,
        run_deadline: Utc::now() + chrono::Duration::minutes(5),
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
async fn drive_bridge(bridge: &mut BridgeProcess) -> (Value, Value) {
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

#[tokio::test]
async fn the_bridge_carries_a_full_tool_conversation_to_the_run_scoped_endpoint() {
    // The bridge is the only thing standing between an ACP adapter and Lemma's
    // tools. Before this test the whole subprocess was unexercised: a bridge
    // that dropped the credential, mangled JSON-RPC framing, or never forwarded
    // `tools/call` would have failed no test.
    let endpoint = LemmaMcpEndpoint::start(McpTransport::StatelessJson).await;
    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    let target_id = Uuid::new_v4();
    let run_id = Uuid::new_v4();
    journal_run(&paths, target_id, run_id, endpoint.run_configuration());

    let mut bridge = BridgeProcess::spawn(directory.path(), target_id, run_id);
    let (listed, called) = drive_bridge(&mut bridge).await;
    let output = bridge.finish().await;

    assert_eq!(listed["result"]["tools"][0]["name"], "lemma_echo");
    assert_eq!(
        called["result"]["content"][0]["text"], "lemma-echo:BRIDGE_ROUND_TRIP",
        "the tool result did not come back through the bridge"
    );
    assert!(
        output.status.success(),
        "bridge exited with {:?}: {}",
        output.status,
        String::from_utf8_lossy(&output.stderr)
    );

    let requests = endpoint.requests();
    assert_eq!(
        endpoint.methods(),
        vec![
            "initialize",
            "notifications/initialized",
            "tools/list",
            "tools/call"
        ],
        "every JSON-RPC message must reach Lemma, including the notification"
    );
    for record in &requests {
        assert_eq!(
            record.authorization.as_deref(),
            Some("Bearer hermetic-run-scoped-mcp-token"),
            "the run-scoped credential must be attached to every request"
        );
        assert_eq!(
            record.agent_run_id.as_deref(),
            Some(run_id.to_string().as_str()),
            "Lemma scopes tools to the run using this header"
        );
        assert_eq!(
            record.protocol_version.as_deref(),
            Some("2025-06-18"),
            "the client's negotiated protocol version must be echoed upstream"
        );
        assert!(
            record
                .accept
                .as_deref()
                .is_some_and(|accept| accept.contains("text/event-stream")),
            "streamable-HTTP servers reject a client that will not accept SSE"
        );
    }
    assert_eq!(
        requests[0].conversation_id,
        endpoint.conversation_id.to_string(),
        "the conversation id in the URL is how Lemma resolves the toolset"
    );
}

#[tokio::test]
async fn the_bridge_understands_a_server_sent_event_response_and_closes_its_session() {
    // Lemma mounts FastMCP with `json_response=True`, but the bridge advertises
    // `Accept: text/event-stream` and so may be answered that way by any
    // streamable-HTTP deployment. Getting this wrong looks like an agent whose
    // Lemma tools simply never return.
    let endpoint = LemmaMcpEndpoint::start(McpTransport::ServerSentEvents).await;
    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    let target_id = Uuid::new_v4();
    let run_id = Uuid::new_v4();
    journal_run(&paths, target_id, run_id, endpoint.run_configuration());

    let mut bridge = BridgeProcess::spawn(directory.path(), target_id, run_id);
    let (_listed, called) = drive_bridge(&mut bridge).await;
    let output = bridge.finish().await;

    assert_eq!(
        called["result"]["content"][0]["text"],
        "lemma-echo:BRIDGE_ROUND_TRIP"
    );
    assert!(output.status.success());
    let sessions = endpoint
        .requests()
        .into_iter()
        .skip(1)
        .filter_map(|record| record.session_id)
        .collect::<Vec<_>>();
    assert!(
        !sessions.is_empty() && sessions.iter().all(|value| value == "hermetic-mcp-session"),
        "a session id handed back by the server must be replayed on later requests"
    );
    assert_eq!(
        endpoint.deletes(),
        vec![Some("hermetic-mcp-session".to_owned())],
        "the bridge must release the server session when the adapter disconnects"
    );
}

#[tokio::test]
async fn a_run_without_mcp_configuration_cannot_open_a_bridge() {
    // `spawn_run` refuses to dispatch a run whose START_RUN payload carried no
    // MCP object. This pins the bridge's own half of that rule so a future
    // caller cannot reach Lemma's tools with an unauthenticated endpoint.
    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    let target_id = Uuid::new_v4();
    let run_id = Uuid::new_v4();
    journal_run(
        &paths,
        target_id,
        run_id,
        json!({"url": "http://127.0.0.1:1/mcp"}),
    );

    let mut bridge = BridgeProcess::spawn(directory.path(), target_id, run_id);
    bridge.notify("notifications/initialized").await;
    let output = bridge.finish().await;

    assert!(
        !output.status.success(),
        "a bridge with no credential must fail loudly rather than serve tools"
    );
    assert!(
        String::from_utf8_lossy(&output.stderr).contains("missing authorization"),
        "stderr should name the missing credential: {}",
        String::from_utf8_lossy(&output.stderr)
    );
}

#[tokio::test]
async fn an_agent_run_discovers_and_calls_a_lemma_tool_through_the_host() {
    // The end-to-end claim: a START_RUN carrying an `mcp` object results in the
    // ACP agent being handed a working Lemma MCP server, and a tool it calls
    // there reaches Lemma with the run's own credential. Every hop is real
    // except the agent's judgement and Lemma itself.
    let endpoint = LemmaMcpEndpoint::start(McpTransport::StatelessJson).await;
    let directory = TempDir::new().unwrap();
    let shims = ShimmedAgents::install(directory.path(), "mcp");
    let control = ControlPlane::start(
        &shims.harness_key,
        "Use the Lemma tools.",
        endpoint.run_configuration(),
        PermissionAnswer::Ignore,
    )
    .await;
    let host = HostProcess::start(directory.path(), &control, &shims).await;

    control
        .wait_for(
            "the run to reach a terminal event",
            Duration::from_secs(90),
            ControlPlane::saw_terminal,
        )
        .await;

    let answer = control.assistant_text();
    assert!(
        answer.contains("LEMMA_MCP_TOOLS:lemma_echo"),
        "the agent did not see Lemma's tool list; answer={answer:?}, host stderr={}",
        host.stderr()
    );
    assert!(
        answer.contains("LEMMA_MCP_RESULT:lemma-echo:LEMMA_MCP_ROUND_TRIP"),
        "the tool result never made it back to the agent; answer={answer:?}"
    );

    // The bridge the agent launched was the one `spawn_run` described, and it
    // authenticated with the run's own credential rather than anything ambient.
    //
    // These assertions read the host's real `session/new` params rather than
    // the agent's report, so they cannot be satisfied by a host that dispatched
    // the run with no MCP server: `mcpServers` would be empty for every session
    // and `served` would be empty too.
    let sessions = shims
        .traffic()
        .into_iter()
        .filter(|entry| entry["message"]["method"] == "session/new")
        .filter_map(|entry| entry["message"]["params"]["mcpServers"].as_array().cloned())
        .collect::<Vec<_>>();
    let served = sessions
        .iter()
        .filter(|servers| !servers.is_empty())
        .collect::<Vec<_>>();
    assert_eq!(
        served.len(),
        1,
        "only the dispatched run may be handed a Lemma server; the capability \
         probes that precede it must open a session with none: {:?}",
        sessions.iter().map(Vec::len).collect::<Vec<_>>()
    );
    let mcp_servers = served[0];
    assert_eq!(mcp_servers.len(), 1);
    assert_eq!(mcp_servers[0]["name"], "lemma");
    let args = mcp_servers[0]["args"]
        .as_array()
        .unwrap()
        .iter()
        .map(|value| value.as_str().unwrap_or_default().to_owned())
        .collect::<Vec<_>>();
    assert!(
        args.contains(&"mcp-bridge".to_owned())
            && args.contains(&control.run_id.to_string())
            && !args.iter().any(|arg| arg.contains("Bearer")),
        "the bridge argv must identify the run without leaking its credential: {args:?}"
    );

    assert_eq!(
        endpoint.methods(),
        vec![
            "initialize",
            "notifications/initialized",
            "tools/list",
            "tools/call"
        ]
    );
    let call = endpoint
        .requests()
        .into_iter()
        .find(|record| record.method == "tools/call")
        .unwrap();
    assert_eq!(call.params["name"], "lemma_echo");
    assert_eq!(call.params["arguments"]["text"], "LEMMA_MCP_ROUND_TRIP");
    assert_eq!(
        call.agent_run_id.as_deref(),
        Some(control.run_id.to_string().as_str()),
        "Lemma must be able to attribute the tool call to this run"
    );
    assert_eq!(
        call.authorization.as_deref(),
        Some(
            endpoint.run_configuration()["authorization"]
                .as_str()
                .unwrap()
        ),
        "the tool call must carry the credential from this run's START_RUN \
         payload and nothing else"
    );

    // The tool call was also reported to Lemma as a durable event, which is
    // what makes it visible in the conversation.
    assert!(
        control
            .events()
            .iter()
            .any(|event| event.event_type == EventType::ToolCallUpsert),
        "a Lemma tool call must reach the conversation as a tool-call event"
    );
    host.shutdown().await;
}

#[tokio::test]
async fn a_refreshed_credential_reaches_the_bridge_without_restarting_the_run() {
    // The credential a run is dispatched with expires in an hour and nothing
    // used to renew it, so a long turn either had to be cut short at that
    // expiry or carry on with every Lemma tool call returning 401 — which the
    // agent experiences as its tools quietly vanishing part-way through a task.
    //
    // Renewal has no channel of its own: Lemma sends a REFRESH_CREDENTIAL
    // command, the supervisor journals it onto the run, and the bridge — a
    // separate process — picks it up because it re-reads its endpoint before
    // every request. This asserts the whole of that, at the only place it can
    // be observed: which bearer the Lemma endpoint actually receives.
    let endpoint = LemmaMcpEndpoint::start(McpTransport::StatelessJson).await;
    let directory = TempDir::new().unwrap();
    let shims = ShimmedAgents::install(directory.path(), "mcp-refresh");
    let control = ControlPlane::start(
        &shims.harness_key,
        "Use the Lemma tools repeatedly.",
        endpoint.run_configuration(),
        PermissionAnswer::Ignore,
    )
    .await;

    let mut renewed = endpoint.run_configuration();
    renewed["authorization"] = json!("Bearer renewed-run-scoped-token");
    renewed["token"] = json!("renewed-run-scoped-token");
    // Lemma serves the replacement it just issued alongside the one still
    // in use, because a call already in flight carries the old one.
    endpoint.also_accept("Bearer renewed-run-scoped-token");
    control.refresh_credential_when_text_contains("LEMMA_MCP_READY", renewed);

    let host = HostProcess::start(directory.path(), &control, &shims).await;
    control
        .wait_for(
            "the run to reach a terminal event",
            Duration::from_secs(90),
            ControlPlane::saw_terminal,
        )
        .await;

    let bearers = endpoint
        .requests()
        .into_iter()
        .filter(|record| record.method == "tools/call")
        .filter_map(|record| record.authorization)
        .collect::<Vec<_>>();
    assert!(
        bearers.len() >= 2,
        "the agent should have made several tool calls, got {bearers:?}"
    );
    assert_eq!(
        bearers.first().map(String::as_str),
        Some("Bearer hermetic-run-scoped-mcp-token"),
        "the run must start on the credential it was dispatched with"
    );
    assert_eq!(
        bearers.last().map(String::as_str),
        Some("Bearer renewed-run-scoped-token"),
        "the bridge never picked up the replacement credential; a long run \
         would keep 401ing until it died. host stderr={}",
        host.stderr()
    );
    // And the agent noticed nothing: it kept working across the change.
    let answer = control.assistant_text();
    assert!(
        answer.contains("LEMMA_MCP_REFRESH_DONE"),
        "the turn should have finished normally, got {answer:?}; bearers={bearers:?}"
    );

    host.shutdown().await;
}
