use super::super::*;

/// A `tools/list` with no `params` -- what Claude Code 2.1.296 sends -- is
/// relayed as an empty object, which Lemma accepts. As `null` it was refused,
/// and the agent ran without any of Lemma's tools.
#[test]
fn a_request_without_params_is_relayed_with_an_empty_object() {
    let omitted = json!({ "jsonrpc": "2.0", "id": 1, "method": "tools/list" });
    let null = json!({ "jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": null });

    assert_eq!(request_params(&omitted), json!({}));
    assert_eq!(request_params(&null), json!({}));
}

#[test]
fn params_that_were_sent_are_relayed_as_they_are() {
    let call = json!({
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": { "name": "lemma_request_approval", "arguments": { "reason": "delete" } },
    });

    assert_eq!(
        request_params(&call),
        json!({ "name": "lemma_request_approval", "arguments": { "reason": "delete" } })
    );
}
