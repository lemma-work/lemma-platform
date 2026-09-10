//! A credential that changed, and one that was refused.

use super::*;

#[tokio::test]
async fn a_restarting_lemma_does_not_take_the_agents_tools_with_it() {
    // The bridge used to `bail!` on any non-2xx, and a bail here ends the
    // process. So one 502 -- a backend restarting, which it does whenever its
    // configuration changes -- closed the adapter's stdio pipe and every Lemma
    // tool vanished for the rest of the run, with no error the agent could see.
    let endpoint = LemmaMcpEndpoint::start(McpTransport::StatelessJson).await;
    endpoint.fail_next([
        ScriptedFailure::Status(reqwest::StatusCode::BAD_GATEWAY),
        ScriptedFailure::Status(reqwest::StatusCode::SERVICE_UNAVAILABLE),
    ]);
    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    let target_id = Uuid::new_v4();
    let run_id = Uuid::new_v4();
    journal_run(&paths, target_id, run_id, endpoint.run_configuration());

    let mut bridge = BridgeProcess::spawn(directory.path(), target_id, run_id);
    let answer = bridge
        .request("initialize", json!({"protocolVersion": "2025-06-18"}))
        .await;
    bridge.finish().await;

    assert!(
        answer.pointer("/result/serverInfo").is_some(),
        "the call should have survived two transient failures: {answer}"
    );
}

#[tokio::test]
async fn a_refused_call_is_answered_rather_than_disconnecting_the_agent() {
    // A 4xx that is not 401/429/408 will fail the same way forever, so it is
    // not retried -- but it still must not be fatal. The agent gets a JSON-RPC
    // error for the call it made, which renders as one failed tool, and keeps
    // every other tool it has.
    let endpoint = LemmaMcpEndpoint::start(McpTransport::StatelessJson).await;
    endpoint.fail_next([ScriptedFailure::Status(reqwest::StatusCode::BAD_REQUEST)]);
    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    let target_id = Uuid::new_v4();
    let run_id = Uuid::new_v4();
    journal_run(&paths, target_id, run_id, endpoint.run_configuration());

    let mut bridge = BridgeProcess::spawn(directory.path(), target_id, run_id);
    let refused = bridge.request("tools/list", json!({})).await;
    // The bridge is still there, and the next call works.
    let served = bridge.request("tools/list", json!({})).await;
    bridge.finish().await;

    assert_eq!(
        refused.pointer("/error/code").and_then(Value::as_i64),
        Some(-32603),
        "a refused call should come back as this call's own error: {refused}"
    );
    assert!(
        served.pointer("/result/tools").is_some(),
        "the bridge must outlive one refused call: {served}"
    );
}

#[tokio::test]
async fn a_refused_credential_is_re_read_from_the_journal_once() {
    // A dead run token never arrives as a 401: Lemma authorizes inside the
    // JSON-RPC handler, so it comes back as HTTP 200 carrying an error object.
    // Nothing the bridge could read off the status line would ever see it, and
    // the credential it needs has meanwhile been journalled by
    // REFRESH_CREDENTIAL.
    let endpoint = LemmaMcpEndpoint::start(McpTransport::StatelessJson).await;
    endpoint.fail_next([ScriptedFailure::Unauthorized]);
    let directory = TempDir::new().unwrap();
    let paths = HostPaths::under(directory.path());
    paths.ensure().unwrap();
    let target_id = Uuid::new_v4();
    let run_id = Uuid::new_v4();
    journal_run(&paths, target_id, run_id, endpoint.run_configuration());

    let mut bridge = BridgeProcess::spawn(directory.path(), target_id, run_id);
    let answer = bridge.request("tools/list", json!({})).await;
    bridge.finish().await;

    assert!(
        answer.pointer("/result/tools").is_some(),
        "the retry after re-reading the credential should have been served: {answer}"
    );
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
