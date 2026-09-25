//! A Lemma that went away, a credential that changed, and one that was refused.

use super::*;

#[tokio::test]
async fn a_restarting_lemma_does_not_take_the_agents_tools_with_it() {
    // The bridge used to `bail!` on any non-2xx, and a bail here ends the
    // process. So one 502 -- a backend restarting, which it does whenever its
    // configuration changes -- closed the adapter's stdio pipe and every Lemma
    // tool vanished for the rest of the run, with no error the agent could see.
    //
    // On the link a restarting Lemma is a connection that closes under the
    // request. A listing is safe to ask again, so the relay waits for the next
    // link and asks there; the agent never hears about it.
    let endpoint = LemmaMcpEndpoint::new();
    endpoint.fail_next([ScriptedFailure::DropLink, ScriptedFailure::DropLink]);
    let directory = TempDir::new().unwrap();
    let (mut bridge, _relay, _target_id, _run_id) =
        bridge_for(&directory, &endpoint, endpoint.run_configuration()).await;

    let answer = bridge.request("tools/list", json!({})).await;
    bridge.finish().await;

    assert!(
        answer.pointer("/result/tools").is_some(),
        "the listing should have survived two dropped links: {answer}"
    );
    assert_eq!(
        endpoint.methods(),
        vec!["tools/list"; 3],
        "the listing is asked again on each new link"
    );
}

#[tokio::test]
async fn a_tool_call_the_link_dropped_is_reported_rather_than_repeated() {
    // The other half of the rule above. A tool call has side effects, and one
    // that was in flight when the link went may already have run, so running
    // it again could do the thing twice. The agent is told, as that call's
    // own error, and keeps its tools.
    let endpoint = LemmaMcpEndpoint::new();
    endpoint.fail_next([ScriptedFailure::DropLink]);
    let directory = TempDir::new().unwrap();
    let (mut bridge, _relay, _target_id, _run_id) =
        bridge_for(&directory, &endpoint, endpoint.run_configuration()).await;

    let dropped = bridge
        .request(
            "tools/call",
            json!({"name": "lemma_echo", "arguments": {"text": "ONCE"}}),
        )
        .await;
    let served = bridge.request("tools/list", json!({})).await;
    bridge.finish().await;

    assert_eq!(
        dropped.pointer("/error/code").and_then(Value::as_i64),
        Some(-32603),
        "a call lost with the link should come back as its own error: {dropped}"
    );
    assert!(
        dropped["error"]["message"]
            .as_str()
            .is_some_and(|message| message.contains("may or may not have run")),
        "the agent has to be told the call may already have run: {dropped}"
    );
    assert_eq!(
        endpoint
            .methods()
            .iter()
            .filter(|method| *method == "tools/call")
            .count(),
        1,
        "a tool call must never be sent twice"
    );
    assert!(
        served.pointer("/result/tools").is_some(),
        "the bridge must outlive the dropped call: {served}"
    );
}

#[tokio::test]
async fn a_transient_refusal_is_retried_and_a_lasting_one_is_answered() {
    // Lemma saying it could not answer *yet* -- its own database or Redis was
    // away -- is retried by the relay, so the agent never sees a blip. One
    // that outlasts the retries comes back as this call's own JSON-RPC error,
    // which renders as one failed tool: the agent keeps every other tool it
    // has, and the next call works.
    let endpoint = LemmaMcpEndpoint::new();
    endpoint.fail_next([ScriptedFailure::Unavailable]);
    let directory = TempDir::new().unwrap();
    let (mut bridge, _relay, _target_id, _run_id) =
        bridge_for(&directory, &endpoint, endpoint.run_configuration()).await;

    let retried = bridge.request("tools/list", json!({})).await;
    endpoint.fail_next([
        ScriptedFailure::Unavailable,
        ScriptedFailure::Unavailable,
        ScriptedFailure::Unavailable,
    ]);
    let refused = bridge.request("tools/list", json!({})).await;
    let served = bridge.request("tools/list", json!({})).await;
    bridge.finish().await;

    assert!(
        retried.pointer("/result/tools").is_some(),
        "one transient refusal should be retried away: {retried}"
    );
    assert_eq!(
        refused.pointer("/error/code").and_then(Value::as_i64),
        Some(-32603),
        "a refusal that lasts should come back as this call's own error: {refused}"
    );
    assert!(
        served.pointer("/result/tools").is_some(),
        "the bridge must outlive one refused call: {served}"
    );
}

#[tokio::test]
async fn a_refused_credential_is_replaced_from_the_journal_on_the_next_call() {
    // A run's credential is replaced in the journal by REFRESH_CREDENTIAL, and
    // the relay reads it from there on every attempt. A refused credential is
    // tried once more -- a replacement may be landing -- and if it is still
    // the stale one, the agent is told for that one call. The next call
    // carries the replacement, without the bridge, the agent or the run being
    // restarted.
    let endpoint = LemmaMcpEndpoint::new();
    let mut stale = endpoint.run_configuration();
    stale["token"] = json!("a-token-lemma-no-longer-accepts");
    let directory = TempDir::new().unwrap();
    let (mut bridge, _relay, target_id, run_id) = bridge_for(&directory, &endpoint, stale).await;

    let refused = bridge.request("tools/list", json!({})).await;
    let journal = Journal::open(&HostPaths::under(directory.path()).journal).unwrap();
    assert!(
        journal
            .refresh_run_mcp(target_id, run_id, 1, &endpoint.run_configuration())
            .unwrap(),
        "the run is in flight, so its credential can be replaced"
    );
    let served = bridge.request("tools/list", json!({})).await;
    bridge.finish().await;

    assert!(
        refused.pointer("/error").is_some(),
        "Lemma refused the stale credential, and the agent is told: {refused}"
    );
    assert!(
        served.pointer("/result/tools").is_some(),
        "the call after the refresh should have been served: {served}"
    );
    assert_eq!(
        endpoint
            .requests()
            .into_iter()
            .map(|record| record.token)
            .collect::<Vec<_>>(),
        vec![
            "a-token-lemma-no-longer-accepts",
            "a-token-lemma-no-longer-accepts",
            MCP_BEARER
        ],
        "each call carries the credential the journal holds when it is made"
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
    // command, the supervisor journals it onto the run, and the relay picks it
    // up because it re-reads the run's credential before every request. This
    // asserts the whole of that, at the only place it can be observed: which
    // token Lemma actually receives.
    let endpoint = LemmaMcpEndpoint::new();
    let directory = TempDir::new().unwrap();
    let shims = ShimmedAgents::install(directory.path(), "mcp-refresh");
    let control = ControlPlane::start(
        &shims.harness_key,
        "Use the Lemma tools repeatedly.",
        endpoint.run_configuration(),
        PermissionAnswer::Ignore,
    )
    .await;
    control.serve_mcp(&endpoint);

    let mut renewed = endpoint.run_configuration();
    renewed["token"] = json!("renewed-run-scoped-token");
    // Lemma serves the replacement it just issued alongside the one still
    // in use, because a call already in flight carries the old one.
    endpoint.also_accept("renewed-run-scoped-token");
    control.refresh_credential_when_text_contains("LEMMA_MCP_READY", renewed);

    let host = HostProcess::start(directory.path(), &control, &shims).await;
    control
        .wait_for(
            "the run to reach a terminal event",
            Duration::from_secs(90),
            ControlPlane::saw_terminal,
        )
        .await;

    let tokens = endpoint
        .requests()
        .into_iter()
        .filter(|record| record.method == "tools/call")
        .map(|record| record.token)
        .collect::<Vec<_>>();
    assert!(
        tokens.len() >= 2,
        "the agent should have made several tool calls, got {tokens:?}"
    );
    assert_eq!(
        tokens.first().map(String::as_str),
        Some(MCP_BEARER),
        "the run must start on the credential it was dispatched with"
    );
    assert_eq!(
        tokens.last().map(String::as_str),
        Some("renewed-run-scoped-token"),
        "the relay never picked up the replacement credential; a long run \
         would keep being refused until it died. host stderr={}",
        host.stderr()
    );
    // And the agent noticed nothing: it kept working across the change.
    let answer = control.assistant_text();
    assert!(
        answer.contains("LEMMA_MCP_REFRESH_DONE"),
        "the turn should have finished normally, got {answer:?}; tokens={tokens:?}"
    );

    host.shutdown().await;
}
