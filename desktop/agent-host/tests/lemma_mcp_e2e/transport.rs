//! Carrying a tool conversation to the run-scoped endpoint.

use super::*;

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
