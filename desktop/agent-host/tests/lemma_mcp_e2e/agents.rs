//! An agent finding and calling a Lemma tool through the host.

use super::*;

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
    // The name Lemma published for this run, not one the host chose. An agent
    // namespaces every MCP tool with the server it came from, so a name of our
    // own here would make the same tool arrive under two names depending on
    // which path ran it — and nothing reading those names could tell.
    assert_eq!(mcp_servers[0]["name"], "lemma_tools");
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

/// Parking, driven through the real bridge subprocess.
///
/// `ask_user` and `request_approval` on an Agent Host run cannot end the
/// agent's turn from inside a tool call, so they answer "waiting for the
/// person" and the bridge does the waiting. This drives that: the stand-in
/// answers parked, holds the decision back for two polls, and the agent must
/// see one ordinary tool result carrying the person's actual answer.
///
/// Deliberately at this level rather than over the helper functions alone.
/// Those were unit-tested first, and deleting the call that wires them into the
/// bridge loop failed nothing -- the agent would have received the raw parked
/// placeholder and had no idea a person was ever asked.
#[tokio::test]
async fn the_bridge_waits_for_a_person_and_answers_with_their_decision() {
    let endpoint = LemmaMcpEndpoint::start(McpTransport::StatelessJson).await;
    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    let target_id = Uuid::new_v4();
    let run_id = Uuid::new_v4();
    journal_run(&paths, target_id, run_id, endpoint.run_configuration());

    let mut bridge = BridgeProcess::spawn(directory.path(), target_id, run_id);
    bridge
        .request(
            "initialize",
            json!({
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "bridge-e2e", "version": "1.0.0"},
            }),
        )
        .await;
    bridge.notify("notifications/initialized").await;
    let called = bridge
        .request(
            "tools/call",
            json!({"name": support::PARK_TOOL, "arguments": {}}),
        )
        .await;
    let output = bridge.finish().await;

    assert!(
        output.status.success(),
        "bridge exited with {:?}: {}",
        output.status,
        String::from_utf8_lossy(&output.stderr)
    );
    // The agent sees the decision, not the placeholder.
    assert_eq!(
        called["result"]["structuredContent"]["answers"]["Pick one"], "Blue",
        "the person's answer never reached the agent: {called}"
    );
    assert!(
        called["result"]["structuredContent"]
            .get("parked_tool_call_id")
            .is_none(),
        "the parked placeholder was left in the result the agent reads"
    );
    // The text beside it has to agree, or a client reading either one sees a
    // decision and a contradiction at the same time.
    let text = called["result"]["content"][0]["text"]
        .as_str()
        .unwrap_or_default();
    assert!(
        text.contains("Blue"),
        "text content still says parked: {text}"
    );
    // And it genuinely waited rather than asking once and getting lucky.
    assert!(
        endpoint.interaction_polls() > 2,
        "the bridge did not keep asking: {} poll(s)",
        endpoint.interaction_polls()
    );
    // The parked call is answered under the durable id Lemma handed back, which
    // is the id the person's approval card resolves through.
    assert_eq!(
        called["result"]["structuredContent"]["decided_for"],
        support::PARK_CALL_ID
    );
}
