//! Lemma's own tool vocabulary, and the shapes its cards read.
//!
//! Every card, icon and approval in the product is keyed on a tool's name, and
//! the pod agent's tools already have names: `exec_command`, `read_file`, and
//! so on. A local agent's `Bash` is the same act as the pod agent's
//! `exec_command`, so it is reported under that name and in that shape. See
//! "Canonical tools" in docs/architecture/agent-host-events.md, which this
//! file implements.

use serde_json::{Map, Value, json};

/// The canonical name for an adapter's own tool name, if it has one.
///
/// Matched case-insensitively on the name with separators removed, so
/// Claude Code's `WebFetch`, OpenCode's `webfetch` and a hypothetical
/// `web_fetch` all land on the same tool.
pub(crate) fn canonical_name(raw: &str) -> Option<&'static str> {
    let key: String = raw
        .chars()
        .filter(|character| character.is_ascii_alphanumeric())
        .map(|character| character.to_ascii_lowercase())
        .collect();
    Some(match key.as_str() {
        "bash" | "shell" | "execcommand" | "exec" | "runcommand" => "exec_command",
        "read" | "readfile" | "view" => "read_file",
        "write" | "writefile" | "create" => "write_file",
        "edit" | "editfile" | "multiedit" | "strreplace" | "patch" | "applypatch" => "edit_file",
        "delete" | "deletefile" => "delete_file",
        "move" | "movefile" | "rename" => "move_file",
        "ls" | "list" | "listfiles" | "listdir" => "list_files",
        "glob" | "find" | "findfiles" => "glob",
        "grep" | "search" | "searchfiles" | "ripgrep" => "grep",
        "websearch" => "web_search",
        "webfetch" | "fetch" => "web_fetch",
        "todowrite" | "updateplan" | "writetodos" => "update_plan",
        "task" | "agent" => "task",
        _ => return None,
    })
}

/// An adapter's tool name, in the spelling Lemma uses for names it does not
/// recognise: `NotebookEdit` becomes `notebook_edit`, `web-fetch` becomes
/// `web_fetch`.
pub(crate) fn snake_case(raw: &str) -> String {
    let mut out = String::with_capacity(raw.len() + 4);
    let mut previous: Option<char> = None;
    for character in raw.trim().chars() {
        if character.is_ascii_uppercase() {
            if previous.is_some_and(|p| p.is_ascii_lowercase() || p.is_ascii_digit()) {
                out.push('_');
            }
            out.push(character.to_ascii_lowercase());
        } else if character.is_ascii_alphanumeric() {
            out.push(character);
        } else if !out.ends_with('_') && !out.is_empty() {
            out.push('_');
        }
        previous = Some(character);
    }
    let trimmed = out.trim_matches('_');
    if trimmed.is_empty() {
        "tool".to_owned()
    } else {
        trimmed.to_owned()
    }
}

fn copy_first(
    target: &mut Map<String, Value>,
    source: &Map<String, Value>,
    name: &str,
    keys: &[&str],
) {
    if target.contains_key(name) {
        return;
    }
    if let Some(value) = keys
        .iter()
        .find_map(|key| source.get(*key).filter(|value| !value.is_null()))
    {
        target.insert(name.to_owned(), value.clone());
    }
}

/// Shells whose `-c` script is the command a person would recognise.
const SHELLS: [&str; 7] = [
    "bash",
    "sh",
    "zsh",
    "/bin/bash",
    "/bin/sh",
    "/bin/zsh",
    "/usr/bin/bash",
];
const SHELL_FLAGS: [&str; 4] = ["-c", "-lc", "-lic", "-ic"];

/// A command as the text a terminal card shows.
///
/// An argv is joined as it would be typed, and a `bash -lc '<script>'`
/// wrapper -- Codex sends one around everything -- is reduced to the script,
/// because that is the command the person asked for.
pub(crate) fn command_text(command: &Value) -> Option<String> {
    match command {
        Value::String(text) => {
            let text = text.trim();
            (!text.is_empty()).then(|| without_shell_wrapper(text))
        }
        Value::Array(parts) => {
            let parts: Vec<&str> = parts.iter().filter_map(Value::as_str).collect();
            if parts.len() != command.as_array().map_or(0, Vec::len) || parts.is_empty() {
                return None;
            }
            if parts.len() >= 3 && SHELLS.contains(&parts[0]) && SHELL_FLAGS.contains(&parts[1]) {
                let script = parts[2].trim();
                return (!script.is_empty()).then(|| script.to_owned());
            }
            Some(
                parts
                    .iter()
                    .map(|part| shell_quote(part))
                    .collect::<Vec<_>>()
                    .join(" "),
            )
        }
        _ => None,
    }
}

fn without_shell_wrapper(command: &str) -> String {
    for shell in SHELLS {
        for flag in SHELL_FLAGS {
            let prefix = format!("{shell} {flag} ");
            if let Some(rest) = command.strip_prefix(&prefix) {
                let rest = rest.trim();
                let unquoted = rest
                    .strip_prefix('\'')
                    .and_then(|rest| rest.strip_suffix('\''))
                    .or_else(|| {
                        rest.strip_prefix('"')
                            .and_then(|rest| rest.strip_suffix('"'))
                    })
                    .unwrap_or(rest);
                return unquoted.to_owned();
            }
        }
    }
    command.to_owned()
}

fn shell_quote(part: &str) -> String {
    if !part.is_empty()
        && part
            .chars()
            .all(|c| c.is_ascii_alphanumeric() || "-_./=:,@%+".contains(c))
    {
        part.to_owned()
    } else {
        format!("'{}'", part.replace('\'', r"'\''"))
    }
}

/// The path of the first location an adapter reported, if any.
pub(crate) fn first_location(locations: &[Value]) -> Option<String> {
    locations
        .iter()
        .find_map(|location| location.get("path").and_then(Value::as_str))
        .map(str::to_owned)
}

/// Every `diff` content block, as the `changes` an edit card reads.
pub(crate) fn diff_changes(content: &[Value]) -> Vec<Value> {
    content
        .iter()
        .filter(|block| block.get("type").and_then(Value::as_str) == Some("diff"))
        .map(|block| {
            let old = block.get("oldText").cloned().unwrap_or(Value::Null);
            let new = block.get("newText").cloned().unwrap_or(Value::Null);
            let kind = block
                .pointer("/_meta/kind")
                .and_then(Value::as_str)
                .map_or_else(
                    || if old.is_null() { "add" } else { "update" },
                    |kind| match kind {
                        "add" | "create" => "add",
                        "delete" | "remove" => "delete",
                        _ => "update",
                    },
                );
            json!({
                "file_path": block.get("path").cloned().unwrap_or(Value::Null),
                "kind": kind,
                "old_text": old,
                "new_text": new,
            })
        })
        .collect()
}

/// A canonical tool's arguments, from whatever the adapter called them.
///
/// Unknown keys are kept rather than dropped: a card that does not read them
/// loses nothing, and someone looking at the raw call still sees everything
/// the agent asked for.
pub(crate) fn canonical_input(
    name: &str,
    raw: &Value,
    locations: &[Value],
    content: &[Value],
) -> Value {
    let source = raw.as_object().cloned().unwrap_or_default();
    let mut input = Map::new();
    match name {
        "exec_command" => {
            if let Some(command) = source
                .get("cmd")
                .or_else(|| source.get("command"))
                .and_then(command_text)
            {
                input.insert("cmd".to_owned(), Value::String(command));
            }
            copy_first(&mut input, &source, "workdir", &["workdir", "cwd"]);
            copy_first(&mut input, &source, "description", &["description"]);
            copy_first(
                &mut input,
                &source,
                "timeout",
                &["timeout", "timeout_ms", "timeout_secs"],
            );
        }
        "read_file" | "write_file" | "edit_file" | "delete_file" => {
            copy_first(
                &mut input,
                &source,
                "file_path",
                &["file_path", "filePath", "path", "filepath", "target_file"],
            );
            if !input.contains_key("file_path")
                && let Some(path) = first_location(locations)
            {
                input.insert("file_path".to_owned(), Value::String(path));
            }
            match name {
                "read_file" => {
                    copy_first(&mut input, &source, "offset", &["offset"]);
                    copy_first(&mut input, &source, "limit", &["limit"]);
                }
                "write_file" => {
                    copy_first(&mut input, &source, "content", &["content", "text"]);
                }
                "edit_file" => {
                    copy_first(
                        &mut input,
                        &source,
                        "old_string",
                        &["old_string", "oldString"],
                    );
                    copy_first(
                        &mut input,
                        &source,
                        "new_string",
                        &["new_string", "newString"],
                    );
                    copy_first(
                        &mut input,
                        &source,
                        "replace_all",
                        &["replace_all", "replaceAll"],
                    );
                    if let Some(Value::Array(edits)) = source.get("edits") {
                        input.insert(
                            "changes".to_owned(),
                            Value::Array(
                                edits
                                    .iter()
                                    .map(|edit| {
                                        json!({
                                            "file_path": input.get("file_path").cloned().unwrap_or(Value::Null),
                                            "kind": "update",
                                            "old_text": edit.get("old_string").cloned().unwrap_or(Value::Null),
                                            "new_text": edit.get("new_string").cloned().unwrap_or(Value::Null),
                                        })
                                    })
                                    .collect(),
                            ),
                        );
                    }
                    let changes = diff_changes(content);
                    if !changes.is_empty() && !input.contains_key("changes") {
                        input.insert("changes".to_owned(), Value::Array(changes));
                    }
                }
                _ => {}
            }
        }
        "move_file" => {
            copy_first(
                &mut input,
                &source,
                "source",
                &["source", "from", "src", "old_path", "file_path"],
            );
            copy_first(
                &mut input,
                &source,
                "destination",
                &["destination", "to", "dest", "new_path"],
            );
        }
        "list_files" | "glob" | "grep" => {
            copy_first(&mut input, &source, "pattern", &["pattern", "query"]);
            copy_first(&mut input, &source, "path", &["path", "cwd", "directory"]);
            if !input.contains_key("path")
                && name == "list_files"
                && let Some(path) = first_location(locations)
            {
                input.insert("path".to_owned(), Value::String(path));
            }
            copy_first(&mut input, &source, "glob", &["glob", "include"]);
        }
        "web_search" => {
            copy_first(&mut input, &source, "query", &["query", "q", "search"]);
        }
        "web_fetch" => {
            copy_first(&mut input, &source, "url", &["url", "uri"]);
            if !input.contains_key("url")
                && let Some(url) = source
                    .get("urls")
                    .and_then(Value::as_array)
                    .and_then(|urls| urls.first())
            {
                input.insert("url".to_owned(), url.clone());
            }
            copy_first(&mut input, &source, "prompt", &["prompt"]);
        }
        "update_plan" => {
            copy_first(&mut input, &source, "todos", &["todos", "entries", "plan"]);
        }
        "task" => {
            copy_first(&mut input, &source, "description", &["description"]);
            copy_first(&mut input, &source, "prompt", &["prompt"]);
            copy_first(
                &mut input,
                &source,
                "subagent_type",
                &["subagent_type", "subagentType", "agent"],
            );
        }
        _ => return raw.clone(),
    }
    // Keep everything the mapping did not consume, under its own name.
    for (key, value) in source {
        let consumed = [
            "cmd",
            "command",
            "cwd",
            "filePath",
            "file_path",
            "path",
            "oldString",
            "newString",
            "old_string",
            "new_string",
            "replaceAll",
            "edits",
            "urls",
            "entries",
            "subagentType",
            "query",
        ];
        if !input.contains_key(&key) && !consumed.contains(&key.as_str()) {
            input.insert(key, value);
        }
    }
    Value::Object(input)
}

/// Text carried in ACP `content` blocks: `{"type":"content","content":{text}}`.
pub(crate) fn content_text(content: &[Value]) -> Option<String> {
    let parts: Vec<&str> = content
        .iter()
        .filter_map(|block| {
            block
                .pointer("/content/text")
                .or_else(|| block.get("text"))
                .and_then(Value::as_str)
        })
        .collect();
    (!parts.is_empty()).then(|| parts.join("\n"))
}

/// Text in a list of MCP / Anthropic content blocks: `[{type:"text",text}]`.
pub(crate) fn blocks_text(value: &Value) -> Option<String> {
    let blocks = value.as_array()?;
    let parts: Vec<&str> = blocks
        .iter()
        .filter(|block| block.get("type").and_then(Value::as_str) == Some("text"))
        .filter_map(|block| block.get("text").and_then(Value::as_str))
        .collect();
    (!parts.is_empty() && parts.len() == blocks.len()).then(|| parts.join("\n"))
}

/// The value an MCP tool itself returned, out of whichever envelope carried it.
///
/// Adapters report an MCP result three ways. Codex wraps the whole
/// `CallToolResult` as `{result: {content, structuredContent}, error}`. Claude
/// Code passes the Anthropic tool-result content through: a list of text
/// blocks. OpenCode stringifies the text into `{output: "..."}`. Lemma's tools
/// return a JSON object, and a card reads its fields, so that object is what
/// the result is.
pub(crate) fn mcp_result(raw: &Value, content: &[Value]) -> Value {
    let envelope = raw.get("result").unwrap_or(raw);
    if let Some(structured) = envelope
        .get("structuredContent")
        .filter(|value| !value.is_null())
    {
        return structured.clone();
    }
    let text = envelope
        .get("content")
        .and_then(blocks_text)
        .or_else(|| blocks_text(envelope))
        .or_else(|| {
            envelope
                .get("output")
                .and_then(Value::as_str)
                .map(str::to_owned)
        })
        .or_else(|| envelope.as_str().map(str::to_owned))
        .or_else(|| content_text(content));
    match text {
        Some(text) => match serde_json::from_str::<Value>(&text) {
            Ok(value @ Value::Object(_)) => value,
            _ => json!({ "output": text }),
        },
        None if raw.is_null() => Value::Null,
        None => raw.clone(),
    }
}

/// Plain text an adapter reported as a tool's output, however it was framed.
pub(crate) fn output_text(raw: &Value, content: &[Value]) -> Option<String> {
    if let Some(text) = raw.as_str() {
        return Some(text.to_owned());
    }
    if let Some(text) = blocks_text(raw) {
        return Some(text);
    }
    if let Some(object) = raw.as_object() {
        for key in [
            "stdout",
            "aggregated_output",
            "formatted_output",
            "output",
            "content",
            "text",
        ] {
            match object.get(key) {
                Some(Value::String(text)) if !text.is_empty() => return Some(text.clone()),
                Some(value @ Value::Array(_)) => {
                    if let Some(text) = blocks_text(value) {
                        return Some(text);
                    }
                }
                _ => {}
            }
        }
    }
    content_text(content)
}

/// A shell command's result, as the terminal card reads it.
pub(crate) fn command_result(raw: &Value, content: &[Value], meta: &Value) -> Value {
    let mut result = Map::new();
    let fields = raw.as_object();
    let exit = fields
        .and_then(|fields| fields.get("exit_code").or_else(|| fields.get("exitCode")).cloned())
        .or_else(|| raw.pointer("/metadata/exit").cloned())
        .or_else(|| meta.pointer("/terminal_exit/exit_code").cloned());
    if let Some(exit) = exit.and_then(|exit| exit.as_i64()) {
        result.insert("exit_code".to_owned(), Value::from(exit));
    }
    let stdout = fields
        .and_then(|fields| {
            ["stdout", "aggregated_output", "formatted_output", "output"]
                .iter()
                .find_map(|key| fields.get(*key).and_then(Value::as_str))
                .filter(|text| !text.is_empty())
                .map(str::to_owned)
        })
        .or_else(|| raw.as_str().map(str::to_owned))
        .or_else(|| blocks_text(raw))
        .or_else(|| content_text(content))
        .or_else(|| {
            meta.pointer("/terminal_output_delta/data")
                .or_else(|| meta.pointer("/terminal_output/data"))
                .and_then(Value::as_str)
                .map(str::to_owned)
        });
    if let Some(stdout) = stdout.filter(|text| !text.is_empty()) {
        result.insert("stdout".to_owned(), Value::String(stdout));
    }
    if let Some(stderr) = fields
        .and_then(|fields| fields.get("stderr"))
        .and_then(Value::as_str)
        .filter(|text| !text.is_empty())
    {
        result.insert("stderr".to_owned(), Value::String(stderr.to_owned()));
    }
    Value::Object(result)
}

/// What a finished canonical tool returned, in the shape its card reads.
pub(crate) fn canonical_output(
    name: &str,
    raw: &Value,
    content: &[Value],
    meta: &Value,
) -> Value {
    match name {
        "exec_command" => command_result(raw, content, meta),
        "read_file" => {
            // The display text first: OpenCode's `output` wraps the file in
            // `<path>`/`<content>` markup with line numbers, and its content
            // block carries the file itself.
            let text = content_text(content)
                .or_else(|| raw.pointer("/metadata/preview").and_then(Value::as_str).map(str::to_owned))
                .or_else(|| output_text(raw, &[]));
            text.map_or(Value::Null, |text| json!({ "content": text }))
        }
        "write_file" | "edit_file" | "delete_file" | "move_file" => {
            let mut result = Map::new();
            if let Some(text) = content_text(content).or_else(|| output_text(raw, &[])) {
                result.insert("message".to_owned(), Value::String(text));
            }
            let changes = diff_changes(content);
            if !changes.is_empty() {
                result.insert("changes".to_owned(), Value::Array(changes));
            }
            Value::Object(result)
        }
        "update_plan" => raw
            .get("todos")
            .map(|todos| json!({ "todos": todos }))
            .unwrap_or_else(|| raw.clone()),
        _ => output_text(raw, content).map_or_else(
            || if raw.is_null() { Value::Null } else { raw.clone() },
            |text| json!({ "output": text }),
        ),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn a_shell_wrapper_is_reduced_to_the_script() {
        assert_eq!(
            command_text(&json!(["bash", "-lc", "pwd"])).as_deref(),
            Some("pwd")
        );
        assert_eq!(
            command_text(&json!("/bin/zsh -lc 'ls -la'")).as_deref(),
            Some("ls -la")
        );
        assert_eq!(
            command_text(&json!(["git", "commit", "-m", "a message"])).as_deref(),
            Some("git commit -m 'a message'")
        );
    }

    #[test]
    fn names_are_recognised_whatever_their_spelling() {
        assert_eq!(canonical_name("Bash"), Some("exec_command"));
        assert_eq!(canonical_name("WebFetch"), Some("web_fetch"));
        assert_eq!(canonical_name("webfetch"), Some("web_fetch"));
        assert_eq!(canonical_name("todowrite"), Some("update_plan"));
        assert_eq!(canonical_name("NotebookEdit"), None);
        assert_eq!(snake_case("NotebookEdit"), "notebook_edit");
        assert_eq!(snake_case("tinyfish_search"), "tinyfish_search");
        assert_eq!(snake_case("web-fetch"), "web_fetch");
    }

    #[test]
    fn every_mcp_envelope_yields_the_tools_own_object() {
        let expected = json!({ "success": true, "resource_id": "resource-1" });
        // Codex
        let codex = json!({ "result": { "content": [{ "type": "text", "text": "{\"success\": true, \"resource_id\": \"resource-1\"}" }], "structuredContent": expected }, "error": null });
        assert_eq!(mcp_result(&codex, &[]), expected);
        // Claude Code
        let claude = json!([{ "type": "text", "text": "{\"success\": true, \"resource_id\": \"resource-1\"}" }]);
        assert_eq!(mcp_result(&claude, &[]), expected);
        // OpenCode
        let opencode = json!({ "output": "{\"success\": true, \"resource_id\": \"resource-1\"}", "metadata": {} });
        assert_eq!(mcp_result(&opencode, &[]), expected);
    }

    #[test]
    fn a_plain_text_result_is_kept_as_output() {
        assert_eq!(
            mcp_result(&json!([{ "type": "text", "text": "not json" }]), &[]),
            json!({ "output": "not json" })
        );
    }
}
