//! A tool call parked on a person's decision, and the result that answers it.

use super::Value;

/// Key Lemma sets on a tool result when it is waiting for a person rather than
/// answering. `ask_user` and `request_approval` return it on an Agent Host run:
/// they cannot end the agent's turn from inside a tool call, so the wait
/// happens in the bridge instead.
pub(crate) const PARKED_KEY: &str = "parked_tool_call_id";

/// The parked id on a `tools/call` result, if it carries one.
pub(crate) fn parked_tool_call_id(result: &Value) -> Option<String> {
    result
        .pointer("/structuredContent")?
        .get(PARKED_KEY)?
        .as_str()
        .filter(|id| !id.is_empty())
        .map(str::to_owned)
}

/// Put the decision where the parked placeholder was, in both places a client
/// reads a tool result: the structured content and the text beside it.
pub(crate) fn result_with_answer(mut result: Value, answer: Value) -> Option<Value> {
    let text = serde_json::to_string(&answer).ok()?;
    let object = result.as_object_mut()?;
    object.insert("structuredContent".to_owned(), answer);
    if let Some(Value::Array(entries)) = object.get_mut("content") {
        for entry in entries.iter_mut() {
            if entry.get("type").and_then(Value::as_str) == Some("text")
                && let Some(entry) = entry.as_object_mut()
            {
                entry.insert("text".to_owned(), Value::String(text.clone()));
                break;
            }
        }
    }
    Some(result)
}
