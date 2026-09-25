//! Carrying a tool conversation from the agent to Lemma, over the link.

use super::*;

#[tokio::test]
async fn the_bridge_carries_a_full_tool_conversation_to_lemma_over_the_link() {
    // The bridge and the relay are the only things standing between an ACP
    // adapter and Lemma's tools. Before this test the whole subprocess was
    // unexercised: a bridge that dropped the credential, mangled JSON-RPC
    // framing, or never forwarded `tools/call` would have failed no test.
    let endpoint = LemmaMcpEndpoint::new();
    let directory = TempDir::new().unwrap();
    let (mut bridge, _relay, _target_id, run_id) =
        bridge_for(&directory, &endpoint, endpoint.run_configuration()).await;

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

    // Only the tool traffic reaches Lemma. The handshake and the notification
    // are the bridge's to answer; forwarding them would be a round trip per
    // session for nothing, and the link has no frame for a notification.
    assert_eq!(
        endpoint.methods(),
        vec!["tools/list", "tools/call"],
        "every tool request must reach Lemma, and nothing else"
    );
    for record in endpoint.requests() {
        assert_eq!(
            record.token, MCP_BEARER,
            "the run-scoped credential must be attached to every request"
        );
        assert_eq!(
            record.run_id,
            run_id.to_string(),
            "Lemma scopes tools to the run it names"
        );
        assert_eq!(
            record.conversation_id,
            endpoint.conversation_id.to_string(),
            "the conversation is how Lemma resolves the toolset"
        );
    }
}

#[tokio::test]
async fn a_run_without_a_credential_cannot_reach_lemmas_tools() {
    // `spawn_run` refuses to dispatch a run whose START_RUN payload carried no
    // MCP object. This pins the relay's own half of that rule: a run that has
    // no credential of its own is refused before anything is sent, so a future
    // caller cannot reach Lemma's tools on the strength of the host's link.
    let endpoint = LemmaMcpEndpoint::new();
    let directory = TempDir::new().unwrap();
    let (mut bridge, _relay, _target_id, _run_id) =
        bridge_for(&directory, &endpoint, json!({"server_name": "lemma_tools"})).await;

    let refused = bridge.request("tools/list", json!({})).await;
    bridge.finish().await;

    assert_eq!(
        refused.pointer("/error/code").and_then(Value::as_i64),
        Some(-32603),
        "a run with no credential must get an error, not tools: {refused}"
    );
    assert!(
        refused["error"]["message"]
            .as_str()
            .is_some_and(|message| message.contains("no Lemma credential")),
        "the error should name the missing credential: {refused}"
    );
    assert!(
        endpoint.requests().is_empty(),
        "nothing may reach Lemma without the run's own credential: {:?}",
        endpoint.methods()
    );
}

#[tokio::test]
async fn only_a_bridge_holding_the_relay_token_is_served() {
    // The relay listens on loopback, where any process on this machine can
    // connect. What stands between one of them and every running agent's
    // Lemma tools is the random token in the relay's private endpoint file --
    // the same bar as reading a run's credential out of the journal.
    use tokio::io::{AsyncBufReadExt, AsyncWriteExt, BufReader};

    let endpoint = LemmaMcpEndpoint::new();
    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    let target_id = Uuid::new_v4();
    let run_id = Uuid::new_v4();
    journal_run(&paths, target_id, run_id, endpoint.run_configuration());
    let _relay = start_relay(&paths, target_id, &endpoint).await;

    let published: Value = serde_json::from_slice(
        &std::fs::read(lemma_agent_host::mcp_relay::endpoint_path(
            &paths, target_id,
        ))
        .unwrap(),
    )
    .unwrap();
    let port = u16::try_from(published["port"].as_u64().unwrap()).unwrap();
    let stream = tokio::net::TcpStream::connect(("127.0.0.1", port))
        .await
        .unwrap();
    let (reader, mut writer) = stream.into_split();
    let request = json!({
        "id": 1,
        "token": "not-the-relay-token",
        "run_id": run_id,
        "method": "tools/list",
        "params": {},
    });
    writer
        .write_all(format!("{request}\n").as_bytes())
        .await
        .unwrap();
    let answer = tokio::time::timeout(
        Duration::from_secs(10),
        BufReader::new(reader).lines().next_line(),
    )
    .await
    .expect("the relay should hang up on a stranger, not leave it waiting");

    assert!(
        matches!(answer, Ok(None) | Err(_)),
        "a connection with the wrong token must be closed unanswered: {answer:?}"
    );
    assert!(
        endpoint.requests().is_empty(),
        "nothing from an unauthenticated connection may reach Lemma"
    );
}
