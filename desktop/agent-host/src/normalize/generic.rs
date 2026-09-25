//! Any adapter without a dialect of its own: Cursor, and whatever comes next.
//!
//! Only what ACP itself defines is trusted -- `kind`, `locations`, `status`
//! -- because anything else is a guess about an adapter nobody has recorded.
//! When one gets a transcript, it gets a module.

use serde_json::Value;

use super::canonical::{canonical_input, canonical_name, snake_case};
use super::{Call, Report, RunContext, ToolRef, ToolSource};

/// Whether the call's input is final.
///
/// ACP's own signal is status: an adapter moves a call to `in_progress` when
/// it starts running it, and it cannot run with half its input. An update
/// that carries no input at all, after one that did, is the other: the
/// adapter has nothing left to add.
pub(super) fn settled(call: &Call, report: &Report<'_>) -> bool {
    let running = |update: &serde_json::Map<String, Value>| {
        update.get("status").and_then(Value::as_str) == Some("in_progress")
    };
    match report {
        Report::Opened(update) => running(update),
        Report::Updated(update) => {
            running(update) || (!update.contains_key("rawInput") && call.has_input())
        }
    }
}

pub(super) fn identify(call: &Call, context: &RunContext) -> (ToolRef, Value) {
    let title = call.first_title.clone().unwrap_or_default();
    if let Some(rest) = title.strip_prefix("mcp__")
        && let Some((server, tool)) = rest.split_once("__")
    {
        return (context.mcp_tool(server, tool), call.raw_input.clone());
    }
    let by_kind = match call.kind.as_deref() {
        Some("execute") => Some("exec_command"),
        Some("read") => Some("read_file"),
        Some("edit") => Some("edit_file"),
        Some("delete") => Some("delete_file"),
        Some("move") => Some("move_file"),
        Some("fetch") => Some("web_fetch"),
        _ => None,
    };
    match by_kind.or_else(|| canonical_name(&title)) {
        Some(name) => (
            native(name),
            canonical_input(name, &call.raw_input, &call.locations, &call.content),
        ),
        None => (
            native(&snake_case(if title.is_empty() { "tool" } else { &title })),
            call.raw_input.clone(),
        ),
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
