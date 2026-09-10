use super::*;

fn parked_frame(id: &str) -> String {
    serde_json::json!({
        "jsonrpc": "2.0",
        "id": 7,
        "result": {
            "content": [{"type": "text", "text": "{\"parked_tool_call_id\":\"x\"}"}],
            "structuredContent": {"success": true, PARKED_KEY: id},
        }
    })
    .to_string()
}

#[test]
fn a_parked_result_is_recognised_and_an_ordinary_one_is_not() {
    assert_eq!(
        parked_tool_call_id(&parked_frame("call-42")).as_deref(),
        Some("call-42")
    );

    let ordinary = serde_json::json!({
        "jsonrpc": "2.0", "id": 7,
        "result": {"structuredContent": {"success": true, "rows": []}}
    })
    .to_string();
    assert!(parked_tool_call_id(&ordinary).is_none());

    // An empty id would send the poller at a URL that can never resolve.
    assert!(parked_tool_call_id(&parked_frame("")).is_none());
}

#[test]
fn the_decision_replaces_the_placeholder_everywhere_a_client_reads_it() {
    // Clients read either the structured content or the text beside it, so
    // leaving one of them saying "parked" would show the model a decision
    // and a contradiction at the same time.
    let answer = serde_json::json!({"success": true, "answers": {"Pick": "Blue"}});
    let resolved = frame_with_result(&parked_frame("call-42"), answer).expect("rewritten");
    let value: Value = serde_json::from_str(&resolved).unwrap();

    assert!(parked_tool_call_id(&resolved).is_none());
    assert_eq!(
        value.pointer("/result/structuredContent/answers/Pick"),
        Some(&Value::String("Blue".to_owned()))
    );
    let text = value
        .pointer("/result/content/0/text")
        .and_then(Value::as_str)
        .expect("text content");
    assert!(text.contains("Blue"), "{text}");
    // The envelope has to survive: a client matches the response to its
    // request by id.
    assert_eq!(value.get("id"), Some(&Value::from(7)));
}

#[test]
fn the_poll_url_is_the_mcp_url_with_the_interaction_on_it() {
    assert_eq!(
        interactions_url(
            "https://api.lemma.work/agent-runtime/conversations/abc/mcp",
            "call-42"
        )
        .as_deref(),
        Some("https://api.lemma.work/agent-runtime/conversations/abc/interactions/call-42")
    );
    // Trailing slash is how the endpoint is sometimes written.
    assert!(interactions_url("https://x/conversations/abc/mcp/", "c").is_some());
    // Anything that is not the MCP endpoint must not be guessed at.
    assert!(interactions_url("https://x/conversations/abc", "c").is_none());
}
