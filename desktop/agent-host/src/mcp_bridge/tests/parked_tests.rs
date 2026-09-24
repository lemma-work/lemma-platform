use super::*;

fn parked_result(id: &str) -> Value {
    serde_json::json!({
        "content": [{"type": "text", "text": "{\"parked_tool_call_id\":\"x\"}"}],
        "structuredContent": {"success": true, PARKED_KEY: id},
    })
}

#[test]
fn a_parked_result_is_recognised_and_an_ordinary_one_is_not() {
    assert_eq!(
        parked_tool_call_id(&parked_result("call-42")).as_deref(),
        Some("call-42")
    );
    let ordinary = serde_json::json!({"structuredContent": {"success": true, "rows": []}});
    assert!(parked_tool_call_id(&ordinary).is_none());
    // An empty id names nothing Lemma could ever answer.
    assert!(parked_tool_call_id(&parked_result("")).is_none());
}

#[test]
fn the_decision_replaces_the_placeholder_everywhere_a_client_reads_it() {
    // Clients read either the structured content or the text beside it, so
    // leaving one of them saying "parked" would show the model a decision
    // and a contradiction at the same time.
    let answer = serde_json::json!({"success": true, "answers": {"Pick": "Blue"}});
    let resolved = result_with_answer(parked_result("call-42"), answer).expect("rewritten");

    assert!(parked_tool_call_id(&resolved).is_none());
    assert_eq!(
        resolved.pointer("/structuredContent/answers/Pick"),
        Some(&Value::String("Blue".to_owned()))
    );
    let text = resolved
        .pointer("/content/0/text")
        .and_then(Value::as_str)
        .expect("text content");
    assert!(text.contains("Blue"), "{text}");
}
