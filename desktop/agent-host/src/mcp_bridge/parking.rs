//! A tool call parked on a decision, and the frame that answers it.

use super::{
    AUTHORIZATION, PARK_POLL_INTERVAL, PARK_TIMEOUT, PARKED_KEY, ResolvedEndpoint, Value,
    normalize_authorization,
};

/// Wait out any frame that came back parked, and answer with the decision.
///
/// A frame is parked when Lemma could not answer yet because it is waiting for
/// a person -- `ask_user` and `request_approval` on an Agent Host run. Those
/// tools cannot end the agent's turn from inside a tool call, so the waiting is
/// done here, and the agent is simply handed the person's answer as that call's
/// result. From the model's side nothing unusual happened: one tool took a
/// while.
pub(crate) async fn resolve_parked_frames(
    http: &reqwest::Client,
    endpoint: &ResolvedEndpoint,
    frames: Vec<String>,
) -> Vec<String> {
    let mut resolved = Vec::with_capacity(frames.len());
    for frame in frames {
        resolved.push(match parked_tool_call_id(&frame) {
            Some(tool_call_id) => match await_decision(http, endpoint, &tool_call_id).await {
                Some(answer) => frame_with_result(&frame, answer).unwrap_or(frame),
                // Timed out, or the poll never succeeded. The unchanged frame
                // still says it is waiting, which is true and leaves the model
                // able to say so rather than stalling on a promise nobody kept.
                None => frame,
            },
            None => frame,
        });
    }
    resolved
}

/// The parked id on a `tools/call` result, if this frame carries one.
pub(crate) fn parked_tool_call_id(frame: &str) -> Option<String> {
    let value: Value = serde_json::from_str(frame).ok()?;
    value
        .pointer("/result/structuredContent")?
        .get(PARKED_KEY)?
        .as_str()
        .filter(|id| !id.is_empty())
        .map(str::to_owned)
}

/// Poll Lemma until the person decides, or the park times out.
pub(crate) async fn await_decision(
    http: &reqwest::Client,
    endpoint: &ResolvedEndpoint,
    tool_call_id: &str,
) -> Option<Value> {
    let url = interactions_url(&endpoint.url, tool_call_id)?;
    let authorization = normalize_authorization(&endpoint.authorization).ok()?;
    let deadline = tokio::time::Instant::now() + PARK_TIMEOUT;
    tracing::info!(
        tool_call_id,
        "waiting for the user to answer a parked tool call"
    );
    while tokio::time::Instant::now() < deadline {
        match http
            .get(&url)
            .header(AUTHORIZATION, authorization)
            .header("x-lemma-agent-run-id", endpoint.run_id.to_string())
            .send()
            .await
        {
            // 204 is "not decided yet", which is the expected answer for most
            // of the wait; anything else non-2xx is transient and treated the
            // same way, because giving up would strand the agent.
            Ok(response) if response.status() == reqwest::StatusCode::OK => {
                return response.json::<Value>().await.ok();
            }
            Ok(_) => {}
            Err(error) => {
                tracing::debug!(%error, "polling a parked tool call failed; retrying");
            }
        }
        tokio::time::sleep(PARK_POLL_INTERVAL).await;
    }
    tracing::warn!(tool_call_id, "a parked tool call was never answered");
    None
}

/// `.../conversations/{id}/mcp` -> `.../conversations/{id}/interactions/{id}`.
pub(crate) fn interactions_url(mcp_url: &str, tool_call_id: &str) -> Option<String> {
    let base = mcp_url.trim_end_matches('/').strip_suffix("/mcp")?;
    Some(format!("{base}/interactions/{tool_call_id}"))
}

/// Put the decision where the parked placeholder was, in both places a client
/// reads a tool result: the structured content and the text beside it.
pub(crate) fn frame_with_result(frame: &str, answer: Value) -> Option<String> {
    let mut value: Value = serde_json::from_str(frame).ok()?;
    let text = serde_json::to_string(&answer).ok()?;
    let result = value.get_mut("result")?;
    result
        .as_object_mut()?
        .insert("structuredContent".to_owned(), answer);
    if let Some(content) = result.pointer_mut("/content")
        && let Some(entries) = content.as_array_mut()
    {
        for entry in entries.iter_mut() {
            if entry.get("type").and_then(Value::as_str) == Some("text")
                && let Some(object) = entry.as_object_mut()
            {
                object.insert("text".to_owned(), Value::String(text.clone()));
                break;
            }
        }
    }
    serde_json::to_string(&value).ok()
}
