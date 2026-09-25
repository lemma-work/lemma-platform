//! The normalizer's rules, one at a time. The recorded transcripts in
//! `tests/fixtures/acp` check the same rules against real adapter output; these
//! pin each one down where a failure names it.

use serde_json::{Value, json};

use super::*;

fn context() -> RunContext {
    RunContext {
        lemma_server: Some("lemma_tools".into()),
        lemma_tools: ["lemma_exec_command", "lemma_display_resource"]
            .into_iter()
            .map(str::to_owned)
            .collect(),
    }
}

fn normalizer(dialect: Dialect) -> Normalizer {
    Normalizer::new(dialect, context())
}

fn kinds(events: &[Normalized]) -> Vec<EventType> {
    events.iter().map(|event| event.event_type).collect()
}

fn tool(event: &Normalized) -> ToolCallPayload {
    assert_eq!(event.event_type, EventType::ToolCall);
    serde_json::from_value(Value::Object(event.payload.clone().into_iter().collect())).unwrap()
}

fn result(event: &Normalized) -> ToolResultPayload {
    assert_eq!(event.event_type, EventType::ToolCallResult);
    serde_json::from_value(Value::Object(event.payload.clone().into_iter().collect())).unwrap()
}

/// Claude Code streams a call's input as it is written, so the host must not
/// put the call on the record until the adapter says the input is final --
/// a conversation message is appended, never revised.
#[test]
fn a_streamed_claude_call_is_held_until_its_input_is_final() {
    let mut normalizer = normalizer(Dialect::ClaudeCode);
    let opened = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call", "toolCallId": "toolu_1", "status": "pending",
        "rawInput": {}, "title": "Terminal", "kind": "execute",
        "_meta": { "claudeCode": { "toolName": "Bash" } },
    }));
    assert!(opened.is_empty(), "an empty input is not a call yet");
    let partial = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "toolu_1",
        "rawInput": { "command": "ls" }, "title": "ls", "kind": "execute",
        "_meta": { "claudeCode": { "toolName": "Bash" } },
    }));
    assert!(partial.is_empty(), "a streamed prefix is not the input");
    let consolidated = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "toolu_1",
        "rawInput": { "command": "ls -la", "description": "List files" },
        "title": "ls -la", "kind": "execute", "content": [],
        "_meta": { "claudeCode": { "toolName": "Bash" } },
    }));
    assert_eq!(kinds(&consolidated), vec![EventType::ToolCall]);
    let call = tool(&consolidated[0]);
    assert_eq!(call.tool.name, "exec_command");
    assert_eq!(call.tool.source, ToolSource::Native);
    assert_eq!(call.input["cmd"], "ls -la");
    assert_eq!(call.input["description"], "List files");

    let closed = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "toolu_1", "status": "completed",
        "rawOutput": "a\nb\n",
        "_meta": { "claudeCode": { "toolName": "Bash" } },
    }));
    assert_eq!(kinds(&closed), vec![EventType::ToolCallResult]);
    let closed = result(&closed[0]);
    assert_eq!(closed.status, ToolStatus::Completed);
    assert_eq!(closed.output, json!({ "stdout": "a\nb\n" }));
}

/// Claude Code's MCP tools arrive under its own namespacing, and Lemma's are
/// named the way the pod agent names them.
#[test]
fn a_claude_mcp_call_is_lemmas_own_tool() {
    let mut normalizer = normalizer(Dialect::ClaudeCode);
    let events = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call", "toolCallId": "toolu_2", "status": "pending",
        "rawInput": { "type": "MARKDOWN", "content": "# hi" }, "kind": "other",
        "title": "mcp__lemma_tools__lemma_display_resource",
        "_meta": { "claudeCode": { "toolName": "mcp__lemma_tools__lemma_display_resource" } },
    }));
    let call = tool(&events[0]);
    assert_eq!(call.tool.name, "display_resource");
    assert_eq!(call.tool.source, ToolSource::Lemma);
    let closed = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "toolu_2", "status": "completed",
        "rawOutput": [{ "type": "text", "text": "{\"success\": true}" }],
    }));
    assert_eq!(result(&closed[0]).output, json!({ "success": true }));
}

/// Someone else's MCP server keeps its own name, and says whose it is.
#[test]
fn another_servers_tool_is_not_mistaken_for_lemmas() {
    let mut normalizer = normalizer(Dialect::ClaudeCode);
    let events = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call", "toolCallId": "toolu_3",
        "rawInput": { "title": "bug" }, "kind": "other",
        "_meta": { "claudeCode": { "toolName": "mcp__github__create_issue" } },
    }));
    let call = tool(&events[0]);
    assert_eq!(call.tool.name, "create_issue");
    assert_eq!(call.tool.source, ToolSource::Mcp);
    assert_eq!(call.tool.server.as_deref(), Some("github"));
}

/// The bug this module was written for: Codex reports every MCP call with
/// `kind: execute`, and every one of them used to be recorded as a shell
/// command.
#[test]
fn a_codex_mcp_call_is_not_a_shell_command() {
    let mut normalizer = normalizer(Dialect::Codex);
    let events = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call", "toolCallId": "exec-1", "kind": "execute",
        "title": "mcp.lemma_tools.lemma_exec_command", "status": "in_progress",
        "rawInput": { "server": "lemma_tools", "tool": "lemma_exec_command",
                      "arguments": { "cmd": "echo from-mcp" } },
        "_meta": { "is_mcp_tool_call": true },
    }));
    let call = tool(&events[0]);
    assert_eq!(call.tool.name, "exec_command");
    assert_eq!(call.tool.source, ToolSource::Lemma);
    assert_eq!(call.input, json!({ "cmd": "echo from-mcp" }));
}

/// Codex's web search carries its query only on the update that closes it, so
/// it is held until then rather than recorded as a search for nothing.
#[test]
fn a_codex_web_search_waits_for_its_query() {
    let mut normalizer = normalizer(Dialect::Codex);
    let opened = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call", "toolCallId": "ws-1", "kind": "search",
        "title": "Web search", "status": "in_progress",
        "rawInput": { "type": "webSearch", "query": "", "action": null },
    }));
    assert!(opened.is_empty());
    let closed = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "ws-1",
        "title": "Web search: Agent Client Protocol", "status": "completed",
        "rawInput": { "type": "webSearch", "query": "Agent Client Protocol" },
    }));
    assert_eq!(
        kinds(&closed),
        vec![EventType::ToolCall, EventType::ToolCallResult]
    );
    let call = tool(&closed[0]);
    assert_eq!(call.tool.name, "web_search");
    assert_eq!(call.input["query"], "Agent Client Protocol");
}

/// Codex reuses one id for every fuzzy file search. Reopening a closed id is a
/// new call, or the second search disappears into the first.
#[test]
fn a_reused_id_opens_a_new_call() {
    let mut normalizer = normalizer(Dialect::Codex);
    let open = json!({
        "sessionUpdate": "tool_call", "toolCallId": "fuzzyFileSearch.s",
        "kind": "search", "title": "Search for 'x'", "status": "in_progress",
    });
    let close = json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "fuzzyFileSearch.s", "status": "completed",
    });
    let first: Vec<_> = [open.clone(), close.clone()]
        .iter()
        .flat_map(|update| normalizer.session_update_value(update))
        .collect();
    let second: Vec<_> = [open, close]
        .iter()
        .flat_map(|update| normalizer.session_update_value(update))
        .collect();
    assert_eq!(first.len(), 2);
    assert_eq!(second.len(), 2);
    assert_ne!(first[0].object_id, second[0].object_id);
    assert_eq!(second[0].object_id, second[1].object_id);
}

/// `OpenCode` names the tool only in its first title and describes the call in
/// every later one.
#[test]
fn opencode_is_named_by_its_first_title() {
    let mut normalizer = normalizer(Dialect::OpenCode);
    assert!(
        normalizer
            .session_update_value(&json!({
                "sessionUpdate": "tool_call", "toolCallId": "call_1", "title": "bash",
                "kind": "execute", "status": "pending", "rawInput": {},
            }))
            .is_empty()
    );
    let running = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "call_1", "status": "in_progress",
        "title": "echo hi", "kind": "execute", "rawInput": { "command": "echo hi", "cwd": "/w" },
    }));
    let call = tool(&running[0]);
    assert_eq!(call.tool.name, "exec_command");
    assert_eq!(call.tool.title.as_deref(), Some("echo hi"));
    assert_eq!(call.input, json!({ "cmd": "echo hi", "workdir": "/w" }));
    let closed = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "call_1", "status": "completed",
        "rawOutput": { "output": "hi\n", "metadata": { "exit": 0 } },
    }));
    assert_eq!(
        result(&closed[0]).output,
        json!({ "exit_code": 0, "stdout": "hi\n" })
    );
}

/// `OpenCode` joins server and tool with `_`; only Lemma's own are split back
/// apart, against the list the run published.
#[test]
fn opencode_lemma_tools_are_recognised_and_lookalikes_are_not() {
    let mut normalizer = normalizer(Dialect::OpenCode);
    let open = |id: &str, title: &str| {
        json!({ "sessionUpdate": "tool_call", "toolCallId": id, "title": title,
                "kind": "other", "status": "in_progress", "rawInput": { "cmd": "x" } })
    };
    let ours = normalizer.session_update_value(&open("a", "lemma_tools_lemma_exec_command"));
    let ours = tool(&ours[0]);
    assert_eq!(
        (ours.tool.name.as_str(), ours.tool.source),
        ("exec_command", ToolSource::Lemma)
    );
    let unpublished = normalizer.session_update_value(&open("b", "lemma_tools_lemma_rm_rf"));
    let unpublished = tool(&unpublished[0]);
    // Nobody's tool this host knows: reported verbatim, claimed by no card.
    assert_eq!(unpublished.tool.source, ToolSource::Mcp);
    assert_eq!(unpublished.tool.name, "lemma_tools_lemma_rm_rf");
    // Somebody else's server whose joined name spells one of Lemma's tools
    // is not Lemma's tool.
    let lookalike = normalizer.session_update_value(&open("c", "web_search"));
    let lookalike = tool(&lookalike[0]);
    assert_eq!(
        (lookalike.tool.name.as_str(), lookalike.tool.source),
        ("web_search", ToolSource::Mcp)
    );
}

/// A permission request is the adapter saying the input is final: the call it
/// gates goes on the record first, and the request carries its canonical name.
#[test]
fn a_permission_request_releases_the_call_it_gates() {
    let mut normalizer = normalizer(Dialect::ClaudeCode);
    let held = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call", "toolCallId": "toolu_9", "status": "pending",
        "rawInput": {}, "kind": "edit", "_meta": { "claudeCode": { "toolName": "Write" } },
    }));
    assert!(held.is_empty(), "the call waits for its final input");
    let (events, id, fields) = normalizer.permission_request(&json!({
        "sessionId": "s",
        "toolCall": { "toolCallId": "toolu_9", "rawInput": { "file_path": "/a", "content": "x" },
                      "_meta": { "claudeCode": { "toolName": "Write" } } },
        "options": [],
    }));
    assert_eq!(kinds(&events), vec![EventType::ToolCall]);
    assert_eq!(id.as_deref(), Some("toolu_9"));
    assert_eq!(fields["tool"]["name"], "write_file");
    assert_eq!(fields["input"]["file_path"], "/a");
}

/// A plan update is delivered as the call the product already draws a plan
/// card for.
#[test]
fn a_plan_is_an_update_plan_call() {
    let mut normalizer = normalizer(Dialect::ClaudeCode);
    let events = normalizer.session_update_value(&json!({
        "sessionUpdate": "plan",
        "entries": [{ "content": "Read", "status": "completed", "priority": "high" }],
    }));
    assert_eq!(
        kinds(&events),
        vec![EventType::ToolCall, EventType::ToolCallResult]
    );
    assert_eq!(tool(&events[0]).tool.name, "update_plan");
    assert_eq!(result(&events[1]).output["todos"][0]["content"], "Read");
    assert_eq!(events[0].object_id, events[1].object_id);
}

/// `usage_update` is the context window, not token usage: reading it as usage
/// recorded every turn as zero tokens and dozens of requests.
#[test]
fn the_context_window_is_not_usage() {
    let mut normalizer = normalizer(Dialect::Codex);
    let events = normalizer.session_update_value(&json!({
        "sessionUpdate": "usage_update", "used": 20661, "size": 258_400,
    }));
    assert_eq!(kinds(&events), vec![EventType::SessionUpdate]);
    assert_eq!(events[0].payload["context"]["used"], 20661);
    let finished = normalizer.finish(Some(&json!({
        "totalTokens": 21446, "inputTokens": 1217, "cachedReadTokens": 20224, "outputTokens": 5,
    })));
    assert_eq!(kinds(&finished), vec![EventType::Usage]);
    assert_eq!(finished[0].payload["input_tokens"], 1217);
    assert_eq!(finished[0].payload["cached_input_tokens"], 20224);
}

/// A turn that ends with a call still held -- cancelled, or the adapter died
/// -- still owes the conversation that call.
#[test]
fn a_call_still_held_at_the_end_of_the_turn_is_released() {
    let mut normalizer = normalizer(Dialect::ClaudeCode);
    let held = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call", "toolCallId": "toolu_5", "status": "pending",
        "rawInput": {}, "_meta": { "claudeCode": { "toolName": "Read" } },
    }));
    assert!(held.is_empty(), "the call waits for its final input");
    let owed = normalizer.finish(None);
    assert_eq!(kinds(&owed), vec![EventType::ToolCall]);
    assert_eq!(tool(&owed[0]).tool.name, "read_file");
}

/// A result for a call the adapter never opened still pairs with a call.
#[test]
fn a_result_without_an_opening_is_opened_first() {
    let mut normalizer = normalizer(Dialect::Generic);
    let events = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "late", "status": "failed",
        "kind": "execute", "rawInput": { "command": "false" },
        "rawOutput": { "exit_code": 1, "stderr": "boom" },
    }));
    assert_eq!(
        kinds(&events),
        vec![EventType::ToolCall, EventType::ToolCallResult]
    );
    let closed = result(&events[1]);
    assert_eq!(closed.status, ToolStatus::Failed);
    assert_eq!(closed.error.as_deref(), Some("exited with code 1"));
}

/// Claude Code says why a tool result is its own prose rather than the tool's
/// output, and a person reading "failed" for a call they declined is misled.
#[test]
fn a_declined_claude_call_is_denied_not_failed() {
    let mut normalizer = normalizer(Dialect::ClaudeCode);
    let events = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "toolu_7", "status": "failed",
        "rawInput": { "command": "rm -rf /" },
        "_meta": { "claudeCode": { "toolName": "Bash", "nonExecutionKind": "user-rejected" } },
    }));
    let closed = result(&events[1]);
    assert_eq!(closed.status, ToolStatus::Denied);
    assert_eq!(closed.error.as_deref(), Some("not allowed"));
}

/// Live terminal output streams as progress, and a late report about a call
/// already closed changes nothing.
#[test]
fn progress_streams_and_late_reports_are_ignored() {
    let mut normalizer = normalizer(Dialect::ClaudeCode);
    // Opening the call is setup; what it emits is not what this test checks.
    let _ = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call", "toolCallId": "toolu_8", "status": "pending",
        "rawInput": { "command": "make" }, "_meta": { "claudeCode": { "toolName": "Bash" } },
    }));
    let progress = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "toolu_8",
        "_meta": { "terminal_output": { "data": "building\n" } },
    }));
    assert_eq!(kinds(&progress), vec![EventType::ToolCallProgress]);
    assert_eq!(progress[0].payload["text"], "building\n");
    // Closing the call is setup for the late report below.
    let _ = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "toolu_8", "status": "completed",
        "rawOutput": "done",
    }));
    let late = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call_update", "toolCallId": "toolu_8", "title": "renamed",
    }));
    assert!(late.is_empty());
}

#[test]
fn text_chunks_carry_text_and_rich_blocks_carry_their_content() {
    let mut normalizer = normalizer(Dialect::ClaudeCode);
    let text = normalizer.session_update_value(&json!({
        "sessionUpdate": "agent_message_chunk", "content": { "type": "text", "text": "hi" },
    }));
    assert_eq!(kinds(&text), vec![EventType::AgentMessageChunk]);
    assert_eq!(text[0].payload["text"], "hi");
    let image = normalizer.session_update_value(&json!({
        "sessionUpdate": "agent_message_chunk",
        "content": { "type": "image", "data": "cG5n", "mimeType": "image/png" },
    }));
    assert_eq!(image[0].payload["content"]["type"], "image");
    assert!(!image[0].payload.contains_key("text"));
    let echoed = normalizer.session_update_value(&json!({
        "sessionUpdate": "user_message_chunk", "content": { "type": "text", "text": "me" },
    }));
    assert!(
        echoed.is_empty(),
        "the user's own words are not the agent's"
    );
}

#[test]
fn an_over_long_id_is_shortened_the_same_way_everywhere() {
    let long = "call_".to_owned() + &"a".repeat(400);
    let mut normalizer = normalizer(Dialect::Generic);
    let events = normalizer.session_update_value(&json!({
        "sessionUpdate": "tool_call", "toolCallId": long, "kind": "execute",
        "status": "in_progress", "rawInput": { "command": "ls" },
    }));
    let id = events[0].object_id.clone().unwrap();
    assert!(id.len() <= 255);
    let (_, request_id, _) = normalizer.permission_request(&json!({
        "sessionId": "s", "toolCall": { "toolCallId": long }, "options": [],
    }));
    assert_eq!(request_id.as_deref(), Some(id.as_str()));
}
