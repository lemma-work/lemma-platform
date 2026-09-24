//! ACP session updates, turned into the run events Lemma stores.
//!
//! The host is the only component that knows which adapter it is talking to
//! and which pinned version of it (`agent-adapters.lock.json`), so it is the
//! only one that can read each adapter's shapes with certainty. Everything
//! adapter-specific therefore lives here, one module per adapter, and the
//! backend receives typed, adapter-neutral events it maps without guessing.
//! The contract is docs/architecture/agent-host-events.md; the ground truth
//! it is held to is the recorded transcripts in `tests/fixtures/acp`.
//!
//! What this replaced: the host forwarded near-raw ACP JSON and the backend
//! read a tool's name from whichever of five fields came first. Every Codex
//! MCP call became `exec_command` (Codex reports them with `kind: execute`),
//! Claude Code's `Bash` never did, and five tool-name normalizers across three
//! languages disagreed about the rest.

use std::collections::{HashMap, HashSet};

use serde_json::{Map, Value, json};

use crate::protocol::{
    EventType, JsonMap, ToolCallPayload, ToolRef, ToolResultPayload, ToolSource, ToolStatus,
    UsagePayload,
};

mod canonical;
mod claude;
mod codex;
mod generic;
mod opencode;

#[cfg(test)]
mod tests;


/// One event, ready for the journal.
#[derive(Clone, Debug, PartialEq)]
pub struct Normalized {
    pub event_type: EventType,
    pub object_id: Option<String>,
    pub payload: JsonMap,
}

impl Normalized {
    fn new(event_type: EventType, object_id: Option<String>, payload: Value) -> Self {
        let payload = match payload {
            Value::Object(object) => object.into_iter().collect(),
            _ => JsonMap::new(),
        };
        Self {
            event_type,
            object_id,
            payload,
        }
    }
}

/// Which adapter's shapes to read.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Dialect {
    ClaudeCode,
    Codex,
    OpenCode,
    /// Cursor, and any adapter without a recorded transcript: only the ACP
    /// standard fields (`kind`, `locations`) are trusted.
    Generic,
}

impl Dialect {
    /// The dialect for a harness key from the adapter manifest.
    #[must_use]
    pub fn for_harness(harness_key: &str) -> Self {
        match harness_key {
            "claude-code" => Self::ClaudeCode,
            "codex" => Self::Codex,
            "opencode" => Self::OpenCode,
            _ => Self::Generic,
        }
    }
}

/// Every name Lemma's own MCP server has been registered under. The run's
/// published name (`lemma_tools`) first; the others are names earlier builds
/// used and that still appear in stored conversations.
const LEMMA_SERVERS: [&str; 3] = ["lemma_tools", "lemma-tools", "lemma"];
/// The prefix Lemma exports its tool names with (`lemma_exec_command`).
const LEMMA_PREFIX: &str = "lemma_";

/// What the normalizer knows about the run it is reading.
#[derive(Clone, Debug, Default)]
pub struct RunContext {
    /// The name Lemma's MCP server is registered under for this run.
    pub lemma_server: Option<String>,
    /// The exported names of the tools Lemma serves this run
    /// (`lemma_exec_command`, ...), from the run's MCP configuration. When
    /// present, it is what decides a tool is Lemma's: an exact answer to the
    /// question the namespace shapes can only approximate.
    pub lemma_tools: HashSet<String>,
}

impl RunContext {
    /// The context a run's MCP configuration describes.
    #[must_use]
    pub fn from_mcp(mcp: &Value) -> Self {
        Self {
            lemma_server: mcp
                .get("server_name")
                .and_then(Value::as_str)
                .map(str::to_owned),
            lemma_tools: mcp
                .get("tool_names")
                .and_then(Value::as_array)
                .map(|names| {
                    names
                        .iter()
                        .filter_map(Value::as_str)
                        .map(|name| name.trim().to_ascii_lowercase())
                        .collect()
                })
                .unwrap_or_default(),
        }
    }

    fn is_lemma_server(&self, server: &str) -> bool {
        let server = server.trim().to_ascii_lowercase();
        self.lemma_server
            .as_deref()
            .is_some_and(|name| name.eq_ignore_ascii_case(&server))
            || LEMMA_SERVERS.contains(&server.as_str())
    }

    /// A tool from MCP server `server`: Lemma's, named without its prefix, or
    /// somebody else's, named as that server calls it.
    pub(crate) fn mcp_tool(&self, server: &str, tool: &str) -> ToolRef {
        if self.is_lemma_server(server) {
            return ToolRef {
                name: strip_lemma_prefix(tool),
                source: ToolSource::Lemma,
                server: None,
                title: None,
                kind: None,
            };
        }
        ToolRef {
            name: tool.to_owned(),
            source: ToolSource::Mcp,
            server: Some(server.to_owned()),
            title: None,
            kind: None,
        }
    }

    /// A name an adapter joined from server and tool with `separator`
    /// (OpenCode's `lemma_tools_lemma_exec_command`), if the server is ours.
    pub(crate) fn joined_lemma_tool(&self, joined: &str, separator: &str) -> Option<ToolRef> {
        let lowered = joined.to_ascii_lowercase();
        let servers = self
            .lemma_server
            .iter()
            .map(String::as_str)
            .chain(LEMMA_SERVERS);
        for server in servers {
            let prefix = format!("{server}{separator}");
            if let Some(rest) = lowered.strip_prefix(&prefix) {
                let exported = rest.to_owned();
                // A published list is authoritative when there is one.
                if !self.lemma_tools.is_empty()
                    && !self.lemma_tools.contains(&exported)
                    && !self
                        .lemma_tools
                        .contains(&format!("{LEMMA_PREFIX}{exported}"))
                {
                    return None;
                }
                return Some(self.mcp_tool(server, &joined[prefix.len()..]));
            }
        }
        None
    }
}

fn strip_lemma_prefix(tool: &str) -> String {
    tool.strip_prefix(LEMMA_PREFIX).unwrap_or(tool).to_owned()
}

/// What the normalizer has learned about one tool call so far.
#[derive(Clone, Debug, Default)]
pub(crate) struct Call {
    /// The id this call's events carry: the adapter's, shortened, and made
    /// unique when an adapter reuses one.
    id: String,
    /// The title on the call's first report. OpenCode puts the tool's name
    /// there and replaces it with a description on the next update.
    pub(crate) first_title: Option<String>,
    pub(crate) title: Option<String>,
    pub(crate) kind: Option<String>,
    /// The name the adapter itself reported, where it has a field for one
    /// (Claude Code's `_meta.claudeCode.toolName`).
    pub(crate) reported_name: Option<String>,
    pub(crate) parent: Option<String>,
    pub(crate) raw_input: Value,
    pub(crate) locations: Vec<Value>,
    pub(crate) content: Vec<Value>,
    pub(crate) meta: Map<String, Value>,
    pub(crate) raw_output: Value,
    /// Set once the call has gone out as `tool_call`; never changes after.
    announced: Option<ToolRef>,
    closed: bool,
}

impl Call {
    /// Fold one report of this call into what is known. Empty values never
    /// overwrite full ones: an adapter refines a call in pieces, and the
    /// emptiest piece must not win.
    fn absorb(&mut self, update: &Map<String, Value>) {
        if let Some(title) = non_empty(update.get("title")) {
            if self.first_title.is_none() {
                self.first_title = Some(title.clone());
            }
            self.title = Some(title);
        }
        if let Some(kind) = non_empty(update.get("kind")) {
            self.kind = Some(kind);
        }
        if let Some(raw) = update.get("rawInput").filter(|value| !is_empty(value)) {
            match (&mut self.raw_input, raw) {
                (Value::Object(existing), Value::Object(incoming)) => {
                    for (key, value) in incoming {
                        if !is_empty(value) {
                            existing.insert(key.clone(), value.clone());
                        }
                    }
                }
                (slot, value) => *slot = value.clone(),
            }
        }
        if let Some(Value::Array(locations)) = update.get("locations")
            && !locations.is_empty()
        {
            self.locations.clone_from(locations);
        }
        if let Some(Value::Array(content)) = update.get("content")
            && !content.is_empty()
        {
            self.content.clone_from(content);
        }
        if let Some(Value::Object(meta)) = update.get("_meta") {
            for (key, value) in meta {
                match (self.meta.get_mut(key), value) {
                    (Some(Value::Object(existing)), Value::Object(incoming)) => {
                        for (inner, value) in incoming {
                            existing.insert(inner.clone(), value.clone());
                        }
                    }
                    _ => {
                        self.meta.insert(key.clone(), value.clone());
                    }
                }
            }
        }
        if let Some(raw) = update.get("rawOutput").filter(|value| !value.is_null()) {
            self.raw_output = raw.clone();
        }
    }

    pub(crate) fn has_input(&self) -> bool {
        !is_empty(&self.raw_input)
    }

    pub(crate) fn meta(&self) -> Value {
        Value::Object(self.meta.clone())
    }
}

fn non_empty(value: Option<&Value>) -> Option<String> {
    value
        .and_then(Value::as_str)
        .map(str::trim)
        .filter(|value| !value.is_empty())
        .map(str::to_owned)
}

fn is_empty(value: &Value) -> bool {
    match value {
        Value::Null => true,
        Value::Object(object) => object.is_empty(),
        Value::Array(array) => array.is_empty(),
        Value::String(text) => text.is_empty(),
        _ => false,
    }
}

/// How one report of a call changes what the host should say about it.
enum Report<'a> {
    /// The adapter's first `tool_call` for this id.
    Opened(&'a Map<String, Value>),
    /// A `tool_call_update` that does not close the call.
    Updated(&'a Map<String, Value>),
}

/// Turns one run's ACP updates into Lemma run events.
///
/// Stateful because a tool call is reported in pieces: it is held until its
/// arguments have settled, announced exactly once, and closed exactly once.
pub struct Normalizer {
    dialect: Dialect,
    context: RunContext,
    calls: HashMap<String, Call>,
    /// Calls in the order they opened, so releasing held calls at the end of a
    /// turn is deterministic.
    order: Vec<String>,
    /// The call currently answering to each adapter id. Codex reuses one id for
    /// every fuzzy file search, so a second search would otherwise fold into
    /// the first, closed, call and disappear.
    aliases: HashMap<String, String>,
    counter: u64,
    /// The last anonymous call opened, for an adapter that sends no id at all.
    anonymous_open: Option<String>,
}

impl Normalizer {
    #[must_use]
    pub fn new(dialect: Dialect, context: RunContext) -> Self {
        Self {
            dialect,
            context,
            calls: HashMap::new(),
            order: Vec::new(),
            aliases: HashMap::new(),
            counter: 0,
            anonymous_open: None,
        }
    }

    /// One ACP `session/update`, as the events it amounts to.
    pub fn session_update(
        &mut self,
        update: &agent_client_protocol::schema::v1::SessionUpdate,
    ) -> Vec<Normalized> {
        match serde_json::to_value(update) {
            Ok(value) => self.session_update_value(&value),
            Err(error) => {
                tracing::warn!(%error, "an ACP update would not serialize; dropping it");
                Vec::new()
            }
        }
    }

    /// The same, over the update's JSON.
    #[must_use]
    pub fn session_update_value(&mut self, value: &Value) -> Vec<Normalized> {
        let Some(object) = value.as_object() else {
            return Vec::new();
        };
        let kind = object
            .get("sessionUpdate")
            .and_then(Value::as_str)
            .unwrap_or_default();
        match kind {
            "agent_message_chunk" => vec![text_event(EventType::AgentMessageChunk, object)],
            "agent_thought_chunk" => vec![text_event(EventType::AgentThoughtChunk, object)],
            "tool_call" => self.tool_report(object, true),
            "tool_call_update" => self.tool_report(object, false),
            "plan" => self.plan(object),
            "usage_update" => vec![Normalized::new(
                EventType::SessionUpdate,
                None,
                json!({ "context": {
                    "used": object.get("used").cloned().unwrap_or(Value::Null),
                    "size": object.get("size").cloned().unwrap_or(Value::Null),
                    "cost": object.get("cost").cloned().unwrap_or(Value::Null),
                }}),
            )],
            "available_commands_update" => {
                let names: Vec<Value> = object
                    .get("availableCommands")
                    .and_then(Value::as_array)
                    .map(|commands| {
                        commands
                            .iter()
                            .filter_map(|command| command.get("name").cloned())
                            .collect()
                    })
                    .unwrap_or_default();
                vec![Normalized::new(
                    EventType::SessionUpdate,
                    None,
                    json!({ "commands": names }),
                )]
            }
            "session_info_update" => match non_empty(object.get("title")) {
                Some(title) => vec![Normalized::new(
                    EventType::SessionUpdate,
                    None,
                    json!({ "title": title }),
                )],
                // Codex reports its thread's busy/idle status here and
                // nothing else; that is transport, not a fact about the run.
                None => Vec::new(),
            },
            "current_mode_update" => vec![Normalized::new(
                EventType::SessionUpdate,
                None,
                json!({ "mode": object.get("currentModeId").cloned().unwrap_or(Value::Null) }),
            )],
            "config_option_update" => vec![Normalized::new(
                EventType::ConfigUpdate,
                None,
                json!({
                    "kind": "config_options",
                    "config_options": object.get("configOptions").cloned().unwrap_or(Value::Null),
                }),
            )],
            // The user's own words echoed back; Lemma already has them.
            "user_message_chunk" => Vec::new(),
            other => {
                tracing::debug!(update = other, "ignoring an ACP update this host does not map");
                Vec::new()
            }
        }
    }

    /// A permission request, as the events that go before it and the extra
    /// payload fields it carries.
    ///
    /// The call being gated is folded in and released first: the request is
    /// the adapter saying the input is final, and the approval card has to
    /// follow the call it asks about. Returns the id the request is known by,
    /// shortened the same way as the call's, so the two cannot stop matching
    /// on a long adapter id.
    pub fn permission_request(&mut self, request: &Value) -> (Vec<Normalized>, Option<String>, JsonMap) {
        let mut events = Vec::new();
        let mut extra = JsonMap::new();
        let Some(tool_call) = request.get("toolCall").and_then(Value::as_object) else {
            return (events, None, extra);
        };
        let Some(raw_id) = non_empty(tool_call.get("toolCallId")) else {
            return (events, None, extra);
        };
        let id = self.resolve_id(&raw_id, !self.aliases.contains_key(&raw_id));
        if !self.calls.contains_key(&id) {
            self.order.push(id.clone());
            self.calls.insert(
                id.clone(),
                Call {
                    id: id.clone(),
                    ..Call::default()
                },
            );
        }
        if let Some(call) = self.calls.get_mut(&id) {
            call.absorb(tool_call);
        }
        events.extend(self.announce(&id));
        if let Some(call) = self.calls.get(&id)
            && let Some(tool) = &call.announced
        {
            extra.insert(
                "tool".to_owned(),
                serde_json::to_value(tool).unwrap_or(Value::Null),
            );
            let (_, input) = self.identify(call);
            extra.insert("input".to_owned(), input);
        }
        (events, Some(id), extra)
    }

    /// The events a finished turn still owes: every call that was held and
    /// never released, then the turn's token usage if the adapter reported it.
    pub fn finish(&mut self, usage: Option<&Value>) -> Vec<Normalized> {
        let mut events = Vec::new();
        for id in self.order.clone() {
            events.extend(self.announce(&id));
        }
        if let Some(usage) = usage.and_then(usage_payload) {
            events.push(Normalized::new(
                EventType::Usage,
                None,
                serde_json::to_value(usage).unwrap_or(Value::Null),
            ));
        }
        events
    }

    fn next(&mut self) -> u64 {
        self.counter += 1;
        self.counter
    }

    /// The id a report belongs to, allocating a fresh one when an adapter
    /// reopens an id that already closed.
    fn resolve_id(&mut self, raw: &str, opening: bool) -> String {
        if let Some(current) = self.aliases.get(raw).cloned() {
            let closed = self.calls.get(&current).is_some_and(|call| call.closed);
            if !(opening && closed) {
                return current;
            }
            let fresh = shorten_object_id(format!("{raw}#{}", self.next()));
            self.aliases.insert(raw.to_owned(), fresh.clone());
            return fresh;
        }
        let id = shorten_object_id(raw.to_owned());
        self.aliases.insert(raw.to_owned(), id.clone());
        id
    }

    fn tool_report(&mut self, update: &Map<String, Value>, opening: bool) -> Vec<Normalized> {
        let id = match non_empty(update.get("toolCallId")) {
            Some(raw) => self.resolve_id(&raw, opening),
            // ACP makes the id optional. With none, the only correlation
            // there is is order, so an untagged update belongs to the untagged
            // call still open.
            None if opening => {
                let id = format!("anonymous-tool-call-{}", self.next());
                self.anonymous_open = Some(id.clone());
                id
            }
            None => match self.anonymous_open.clone() {
                Some(id) => id,
                None => return Vec::new(),
            },
        };
        let is_new = !self.calls.contains_key(&id);
        if is_new {
            self.order.push(id.clone());
            self.calls.insert(
                id.clone(),
                Call {
                    id: id.clone(),
                    ..Call::default()
                },
            );
        }
        let call = self.calls.get_mut(&id).expect("inserted above");
        if call.closed {
            // A late report about a call already on the record. Nothing about
            // it can change now: a conversation message is appended, never
            // revised.
            return Vec::new();
        }
        call.absorb(update);
        if call.parent.is_none() {
            call.parent = claude::parent_call(update);
        }
        if call.reported_name.is_none() {
            call.reported_name = claude::reported_name(update);
        }

        let status = update.get("status").and_then(Value::as_str);
        let mut events = Vec::new();
        if let Some(status) = status.and_then(|status| self.closing_status(update, status)) {
            events.extend(self.announce(&id));
            events.extend(self.close(&id, status));
            if self.anonymous_open.as_deref() == Some(id.as_str()) {
                self.anonymous_open = None;
            }
            return events;
        }
        let call = &self.calls[&id];
        let report = if is_new || opening {
            Report::Opened(update)
        } else {
            Report::Updated(update)
        };
        if call.announced.is_none() && self.settled(call, &report) {
            events.extend(self.announce(&id));
        }
        if let Some(text) = progress_text(update) {
            // Output means the tool is running, so its input is final.
            events.extend(self.announce(&id));
            events.push(Normalized::new(
                EventType::ToolCallProgress,
                Some(id),
                json!({ "text": text }),
            ));
        }
        events
    }

    /// Whether this report closes the call, and how.
    fn closing_status(&self, update: &Map<String, Value>, status: &str) -> Option<ToolStatus> {
        let status = match status {
            "completed" => ToolStatus::Completed,
            "failed" => ToolStatus::Failed,
            "cancelled" => ToolStatus::Cancelled,
            _ => return None,
        };
        Some(match self.dialect {
            Dialect::ClaudeCode => claude::refine_status(update, status),
            _ => status,
        })
    }

    /// Whether a call's arguments are final, from what the adapter just said.
    fn settled(&self, call: &Call, report: &Report<'_>) -> bool {
        match self.dialect {
            Dialect::ClaudeCode => claude::settled(call, report),
            Dialect::Codex => codex::settled(call, report),
            Dialect::OpenCode | Dialect::Generic => generic::settled(call, report),
        }
    }

    fn identify(&self, call: &Call) -> (ToolRef, Value) {
        let (mut tool, input) = match self.dialect {
            Dialect::ClaudeCode => claude::identify(call, &self.context),
            Dialect::Codex => codex::identify(call, &self.context),
            Dialect::OpenCode => opencode::identify(call, &self.context),
            Dialect::Generic => generic::identify(call, &self.context),
        };
        if tool.title.is_none() {
            tool.title.clone_from(&call.title);
        }
        if tool.kind.is_none() {
            tool.kind.clone_from(&call.kind);
        }
        (tool, input)
    }

    /// Put a call on the record, if it is not there already.
    fn announce(&mut self, id: &str) -> Vec<Normalized> {
        let Some(call) = self.calls.get(id) else {
            return Vec::new();
        };
        if call.announced.is_some() || call.closed {
            return Vec::new();
        }
        let (tool, input) = self.identify(call);
        let payload = ToolCallPayload {
            tool: tool.clone(),
            input,
            parent_call_id: call.parent.clone(),
        };
        let call = self.calls.get_mut(id).expect("present above");
        call.announced = Some(tool);
        vec![Normalized::new(
            EventType::ToolCall,
            Some(id.to_owned()),
            serde_json::to_value(payload).unwrap_or(Value::Null),
        )]
    }

    fn close(&mut self, id: &str, status: ToolStatus) -> Vec<Normalized> {
        let Some(call) = self.calls.get(id) else {
            return Vec::new();
        };
        let Some(tool) = call.announced.clone() else {
            return Vec::new();
        };
        let output = match tool.source {
            ToolSource::Lemma | ToolSource::Mcp => canonical::mcp_result(&call.raw_output, &call.content),
            ToolSource::Native => match self.dialect {
                Dialect::OpenCode => opencode::output(call, &tool),
                _ => canonical::canonical_output(&tool.name, &call.raw_output, &call.content, &call.meta()),
            },
        };
        let error = failure_sentence(status, &call.raw_output, &output);
        let payload = ToolResultPayload {
            status,
            output,
            error,
        };
        let call = self.calls.get_mut(id).expect("present above");
        call.closed = true;
        vec![Normalized::new(
            EventType::ToolCallResult,
            Some(id.to_owned()),
            serde_json::to_value(payload).unwrap_or(Value::Null),
        )]
    }

    /// An ACP plan update, as the `update_plan` call the product renders a
    /// plan card from. Claude Code reports its to-do list only this way.
    fn plan(&mut self, update: &Map<String, Value>) -> Vec<Normalized> {
        let todos: Vec<Value> = update
            .get("entries")
            .and_then(Value::as_array)
            .map(|entries| {
                entries
                    .iter()
                    .map(|entry| {
                        json!({
                            "content": entry.get("content").cloned().unwrap_or(Value::Null),
                            "status": entry.get("status").cloned().unwrap_or(Value::Null),
                            "priority": entry.get("priority").cloned().unwrap_or(Value::Null),
                        })
                    })
                    .collect()
            })
            .unwrap_or_default();
        let id = format!("plan-{}", self.next());
        let call = ToolCallPayload {
            tool: ToolRef {
                name: "update_plan".to_owned(),
                source: ToolSource::Native,
                server: None,
                title: Some("Plan".to_owned()),
                kind: Some("think".to_owned()),
            },
            input: json!({ "todos": todos }),
            parent_call_id: None,
        };
        let result = ToolResultPayload {
            status: ToolStatus::Completed,
            output: json!({ "todos": todos }),
            error: None,
        };
        vec![
            Normalized::new(
                EventType::ToolCall,
                Some(id.clone()),
                serde_json::to_value(call).unwrap_or(Value::Null),
            ),
            Normalized::new(
                EventType::ToolCallResult,
                Some(id),
                serde_json::to_value(result).unwrap_or(Value::Null),
            ),
        ]
    }
}

/// A text or rich-content chunk. Text is flattened to `text`; the block
/// itself stays in `content`, because an image is carried there and the
/// backend turns it into a pod file.
fn text_event(event_type: EventType, update: &Map<String, Value>) -> Normalized {
    let mut payload = Map::new();
    if let Some(content) = update.get("content") {
        if let Some(text) = content.get("text").and_then(Value::as_str) {
            payload.insert("text".to_owned(), Value::String(text.to_owned()));
        } else {
            payload.insert("content".to_owned(), content.clone());
        }
    }
    let object_id = non_empty(update.get("messageId")).map(shorten_object_id);
    Normalized::new(event_type, object_id, Value::Object(payload))
}

/// Live output of a running call, where an adapter streams one.
///
/// Only explicit terminal deltas: OpenCode repeats a call's *whole* output on
/// each in-progress update, and forwarding that as a delta would print it
/// over and over.
fn progress_text(update: &Map<String, Value>) -> Option<String> {
    let meta = update.get("_meta")?;
    if update.get("status").and_then(Value::as_str) == Some("completed") {
        return None;
    }
    meta.pointer("/terminal_output/data")
        .or_else(|| meta.pointer("/terminal_output_delta/data"))
        .and_then(Value::as_str)
        .filter(|text| !text.is_empty())
        .map(str::to_owned)
}

fn usage_payload(usage: &Value) -> Option<UsagePayload> {
    let number = |keys: &[&str]| {
        keys.iter()
            .find_map(|key| usage.get(*key).and_then(Value::as_u64))
    };
    let payload = UsagePayload {
        input_tokens: number(&["inputTokens", "input_tokens"]),
        output_tokens: number(&["outputTokens", "output_tokens"]),
        cached_input_tokens: number(&["cachedReadTokens", "cached_read_tokens", "cachedInputTokens"]),
        reasoning_tokens: number(&["thoughtTokens", "thought_tokens", "reasoningOutputTokens"]),
        total_tokens: number(&["totalTokens", "total_tokens"]),
    };
    (payload != UsagePayload::default()).then_some(payload)
}

/// Why a call did not complete, in the words a card shows.
fn failure_sentence(status: ToolStatus, raw: &Value, output: &Value) -> Option<String> {
    match status {
        ToolStatus::Completed => None,
        ToolStatus::Denied => Some("not allowed".to_owned()),
        ToolStatus::Cancelled => Some("cancelled".to_owned()),
        ToolStatus::Failed => {
            let stated = raw
                .get("error")
                .and_then(|error| {
                    error
                        .as_str()
                        .map(str::to_owned)
                        .or_else(|| error.get("message").and_then(Value::as_str).map(str::to_owned))
                })
                .filter(|text| !text.trim().is_empty());
            if stated.is_some() {
                return stated;
            }
            if let Some(code) = output.get("exit_code").and_then(Value::as_i64) {
                return Some(format!("exited with code {code}"));
            }
            Some("failed".to_owned())
        }
    }
}

/// The backend stores an event's `object_id` in a 255-character column, and
/// nothing stopped an adapter's id from being longer.
///
/// The cost was out of all proportion to the cause: the batch carrying that id
/// is refused as malformed, and the host eventually discards the run's whole
/// transcript. Truncating alone would collide -- ids that share a long prefix
/// are exactly the shape adapters generate -- so the tail becomes a hash of
/// the original. The id only has to be stable and unique within a run.
#[must_use]
pub fn shorten_object_id(id: String) -> String {
    use sha2::{Digest, Sha256};
    const LIMIT: usize = 255;
    if id.len() <= LIMIT {
        return id;
    }
    let digest = Sha256::digest(id.as_bytes());
    let suffix = format!("-{digest:x}");
    let mut keep = LIMIT - suffix.len();
    while keep > 0 && !id.is_char_boundary(keep) {
        keep -= 1;
    }
    format!("{}{suffix}", &id[..keep])
}
