//! OpenCode, through its native `opencode acp`.
//!
//! OpenCode's tool name is the `title` of the call's *first* report
//! (`bash`, `read`, `edit`, `todowrite`...). Every later update replaces the
//! title with a description of the call -- the command, or the file it
//! touched -- so the name has to be kept from the opening. MCP tools are named
//! by joining server and tool with `_` (`lemma_tools_lemma_exec_command`),
//! which cannot be split for a server this host does not know; only Lemma's
//! own are recognised, and anyone else's is reported under OpenCode's joined
//! name. Results carry the tool's text in `rawOutput.output` and, for a
//! command, the exit status in `rawOutput.metadata.exit`. All of this is read
//! off the transcripts in `tests/fixtures/acp/opencode@*`.

use serde_json::{Value, json};

use super::canonical::{canonical_input, canonical_name, canonical_output, snake_case};
use super::{Call, RunContext, ToolRef, ToolSource};

pub(super) fn identify(call: &Call, context: &RunContext) -> (ToolRef, Value) {
    let name = call
        .first_title
        .clone()
        .unwrap_or_else(|| "tool".to_owned());
    if let Some(tool) = context.joined_lemma_tool(&name, "_") {
        return (tool, call.raw_input.clone());
    }
    match canonical_name(&name) {
        Some(canonical) => (
            native(canonical),
            canonical_input(canonical, &call.raw_input, &call.locations, &call.content),
        ),
        None => (native(&snake_case(&name)), call.raw_input.clone()),
    }
}

/// A native tool's result. OpenCode's `todowrite` reports the list it wrote
/// only as JSON text, so the plan card is fed the list from the call's own
/// input rather than from that text.
pub(super) fn output(call: &Call, tool: &ToolRef) -> Value {
    if tool.name == "update_plan" {
        return json!({ "todos": call.raw_input.get("todos").cloned().unwrap_or(Value::Null) });
    }
    canonical_output(&tool.name, &call.raw_output, &call.content, &call.meta())
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
