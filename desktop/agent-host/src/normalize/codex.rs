//! Codex, through `codex-acp`.
//!
//! Codex names nothing but its MCP calls. Everything else is one of its own
//! parsed actions, reported by ACP kind and a title written for people:
//! a shell command is `kind: execute` with the command in `rawInput`; a file
//! it reads through the shell is `kind: read` titled `Read file '<path>'`
//! with no input at all; an `apply_patch` is `kind: edit` titled `Editing
//! files` with the patch only as diff content. MCP calls are also `kind:
//! execute` -- which is exactly how every one of them used to be mistaken for
//! a shell command -- and are told apart by `_meta.is_mcp_tool_call` and the
//! server and tool in `rawInput`. All of this is read off the transcripts in
//! `tests/fixtures/acp/codex@*`.

use serde_json::{Map, Value, json};

use super::canonical::{canonical_input, diff_changes, first_location, snake_case};
use super::{Call, Report, RunContext, ToolRef, ToolSource};

/// Codex opens a call already `in_progress` with its input complete -- except
/// a web search, whose query only arrives on the update that closes it.
pub(super) fn settled(call: &Call, report: &Report<'_>) -> bool {
    if is_web_search(call) {
        return false;
    }
    super::generic::settled(call, report)
}

fn is_web_search(call: &Call) -> bool {
    call.raw_input.get("type").and_then(Value::as_str) == Some("webSearch")
}

fn is_mcp(call: &Call) -> bool {
    call.meta
        .get("is_mcp_tool_call")
        .and_then(Value::as_bool)
        .unwrap_or(false)
        || (call.raw_input.get("server").is_some() && call.raw_input.get("tool").is_some())
}

pub(super) fn identify(call: &Call, context: &RunContext) -> (ToolRef, Value) {
    if is_mcp(call) {
        let server = call
            .raw_input
            .get("server")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let tool = call
            .raw_input
            .get("tool")
            .and_then(Value::as_str)
            .unwrap_or_default();
        let arguments = call
            .raw_input
            .get("arguments")
            .cloned()
            .unwrap_or_else(|| json!({}));
        return (context.mcp_tool(server, tool), arguments);
    }
    let title = call.title.clone().unwrap_or_default();
    let (name, input) = match call.kind.as_deref() {
        Some("execute") => (
            "exec_command",
            canonical_input("exec_command", &call.raw_input, &call.locations, &call.content),
        ),
        Some("edit") => edit(call),
        Some("delete") => (
            "delete_file",
            canonical_input("delete_file", &call.raw_input, &call.locations, &call.content),
        ),
        Some("move") => (
            "move_file",
            canonical_input("move_file", &call.raw_input, &call.locations, &call.content),
        ),
        Some("read") if title == "List files" || title.starts_with("List files") => (
            "list_files",
            json!({ "path": first_location(&call.locations) }),
        ),
        Some("read") => (
            "read_file",
            json!({
                "file_path": first_location(&call.locations)
                    .or_else(|| quoted(&title))
                    .map_or(Value::Null, Value::String),
            }),
        ),
        Some("search") if is_web_search(call) => ("web_search", web_search_input(call)),
        Some("search") if call.id.starts_with("fuzzyFileSearch") => (
            "glob",
            json!({ "pattern": call.raw_input.get("query").cloned().or_else(|| quoted(&title).map(Value::String)) }),
        ),
        Some("search") => (
            "grep",
            json!({
                "pattern": quoted(&title).map_or(Value::Null, Value::String),
                "path": first_location(&call.locations).map_or(Value::Null, Value::String),
            }),
        ),
        Some("fetch") => (
            "web_fetch",
            canonical_input("web_fetch", &call.raw_input, &call.locations, &call.content),
        ),
        _ => {
            let name = snake_case(if title.is_empty() { "tool" } else { &title });
            return (native(&name), call.raw_input.clone());
        }
    };
    (native(name), strip_nulls(input))
}

/// An `apply_patch`: the patch is only in the diff content. A patch that only
/// adds one file is a write, and is shown as one.
fn edit(call: &Call) -> (&'static str, Value) {
    let changes = diff_changes(&call.content);
    if let [change] = changes.as_slice()
        && change.get("kind").and_then(Value::as_str) == Some("add")
    {
        return (
            "write_file",
            json!({
                "file_path": change.get("file_path").cloned().unwrap_or(Value::Null),
                "content": change.get("new_text").cloned().unwrap_or(Value::Null),
            }),
        );
    }
    let mut input = canonical_input("edit_file", &call.raw_input, &call.locations, &call.content);
    if let Value::Object(object) = &mut input
        && !object.contains_key("file_path")
        && let Some(path) = changes.first().and_then(|change| change.get("file_path")).cloned()
    {
        object.insert("file_path".to_owned(), path);
    }
    ("edit_file", input)
}

fn web_search_input(call: &Call) -> Value {
    let query = call
        .raw_input
        .get("query")
        .and_then(Value::as_str)
        .filter(|query| !query.is_empty())
        .map(str::to_owned)
        .or_else(|| {
            call.raw_input
                .pointer("/action/query")
                .and_then(Value::as_str)
                .map(str::to_owned)
        })
        .or_else(|| {
            call.title
                .as_deref()
                .and_then(|title| title.strip_prefix("Web search: "))
                .map(str::to_owned)
        });
    json!({ "query": query })
}

/// The text between the first pair of single quotes: Codex titles its parsed
/// actions `Read file '<path>'` and `Search for '<pattern>'`.
fn quoted(title: &str) -> Option<String> {
    let start = title.find('\'')? + 1;
    let end = title[start..].rfind('\'')? + start;
    (end > start).then(|| title[start..end].to_owned())
}

fn strip_nulls(value: Value) -> Value {
    match value {
        Value::Object(object) => Value::Object(
            object
                .into_iter()
                .filter(|(_, value)| !value.is_null())
                .collect::<Map<String, Value>>(),
        ),
        other => other,
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

#[cfg(test)]
mod tests {
    use super::quoted;

    #[test]
    fn a_quoted_title_yields_its_subject() {
        assert_eq!(
            quoted("Read file '/workspace/fixture/notes.txt'").as_deref(),
            Some("/workspace/fixture/notes.txt")
        );
        assert_eq!(quoted("Search for 'two'").as_deref(), Some("two"));
        assert_eq!(quoted("List files"), None);
    }
}
