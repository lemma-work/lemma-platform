//! Claude Code, through `claude-agent-acp`.
//!
//! The only adapter that always names its tools: `_meta.claudeCode.toolName`
//! is on the `tool_call` and on every update after it. MCP tools arrive under
//! Claude Code's own namespacing, `mcp__<server>__<tool>`. The adapter streams
//! a call's input as it is written -- the `tool_call` opens with `rawInput:
//! {}`, and each refinement carries a prefix of the fields written so far --
//! and sends the whole call once more, *with* its display `content`, when the
//! model has finished writing it. That last refinement is the signal the input
//! is final. See `toolCallNotification` and `streamedInputRefinement` in the
//! adapter's `acp-agent.js`.

use serde_json::{Map, Value};

use super::canonical::{canonical_input, canonical_name, snake_case};
use super::{Call, Report, RunContext, ToolRef, ToolSource, ToolStatus};

pub(super) fn reported_name(update: &Map<String, Value>) -> Option<String> {
    update
        .get("_meta")
        .and_then(|meta| meta.pointer("/claudeCode/toolName"))
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|name| !name.is_empty())
        .map(str::to_owned)
}

pub(super) fn parent_call(update: &Map<String, Value>) -> Option<String> {
    update
        .get("_meta")
        .and_then(|meta| meta.pointer("/claudeCode/parentToolUseId"))
        .and_then(Value::as_str)
        .filter(|id| !id.is_empty())
        .map(|id| super::shorten_object_id(id.to_owned()))
}

/// Whether the call's input is final.
///
/// The opening report is final only when it already carries input -- a call
/// replayed from history rather than streamed. After that, the refinement
/// that carries the display `content` is the consolidated one: the streamed
/// refinements deliberately never do, because content built from partial
/// input is misleading (an `Edit` missing its `new_string` renders as a pure
/// deletion).
pub(super) fn settled(call: &Call, report: &Report<'_>) -> bool {
    match report {
        Report::Opened(_) => call.has_input(),
        Report::Updated(update) => {
            let running = update.get("status").and_then(Value::as_str) == Some("in_progress");
            let consolidated = update.contains_key("content")
                && update
                    .get("rawInput")
                    .is_some_and(|input| !super::is_empty(input));
            let bare = !update.contains_key("rawInput") && call.has_input();
            running || consolidated || bare
        }
    }
}

/// A closing status, sharpened by the reason Claude Code gives for a tool
/// result that is its own prose rather than the tool's output.
pub(super) fn refine_status(update: &Map<String, Value>, status: ToolStatus) -> ToolStatus {
    if status != ToolStatus::Failed {
        return status;
    }
    match update
        .get("_meta")
        .and_then(|meta| meta.pointer("/claudeCode/nonExecutionKind"))
        .and_then(Value::as_str)
    {
        Some("user-rejected" | "permission-rule") => ToolStatus::Denied,
        Some("interrupted" | "cancelled") => ToolStatus::Cancelled,
        _ => status,
    }
}

pub(super) fn identify(call: &Call, context: &RunContext) -> (ToolRef, Value) {
    let name = call
        .reported_name
        .clone()
        .or_else(|| call.first_title.clone())
        .unwrap_or_else(|| "tool".to_owned());
    if let Some(rest) = name.strip_prefix("mcp__")
        && let Some((server, tool)) = rest.split_once("__")
    {
        return (context.mcp_tool(server, tool), call.raw_input.clone());
    }
    match canonical_name(&name) {
        Some(canonical) => (
            native(canonical),
            canonical_input(canonical, &call.raw_input, &call.locations, &call.content),
        ),
        None => (native(&snake_case(&name)), call.raw_input.clone()),
    }
}

fn native(name: &str) -> ToolRef {
    ToolRef {
        name: name.to_owned(),
        source: ToolSource::Native,
        server: None,
        title: None,
        kind: None,
    }
}
