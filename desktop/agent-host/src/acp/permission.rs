//! Deciding whether a permission request is one Lemma published.

use super::{
    AlwaysAllowOffer, AlwaysAllowScope, HashSet, JsonMap, Map, PermissionOptionKind,
    RequestPermissionRequest, Value, find_string,
};

pub(crate) fn permission_payload(request: &RequestPermissionRequest) -> JsonMap {
    let mut value = serde_json::to_value(request).unwrap_or_else(|_| Value::Object(Map::new()));
    let object = value
        .as_object_mut()
        .expect("permission request serializes as object");
    object.insert(
        "message".to_owned(),
        Value::String(
            "This request controls the local agent's access on this computer.".to_owned(),
        ),
    );
    object
        .iter()
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect()
}

/// The stem every name of Lemma's run-scoped MCP server starts with.
///
/// The server is registered under the name Lemma publishes for the run —
/// `lemma_tools` — and this is the stem shared by that name and the one older
/// hosts registered (`lemma`), which is what the matcher below anchors on. It
/// is also the fallback `runtime.rs` registers under when a run carries no
/// server name of its own.
pub(crate) const SCOPED_MCP_SERVER: &str = "lemma";

/// Is this the agent asking permission to use one of *our* tools?
///
/// Lemma's own MCP tools are already scoped to the run and authorised by the
/// workspace, so prompting for each one is noise the user has to click through
/// on every single call.
///
/// Three ways to tell, because no one of them is enough.
///
/// The `_meta` flag is a Claude Code convention; an agent that does not set it —
/// `OpenCode` does not — had every Lemma tool call raise an approval card. The
/// tool name is checked too, against the server name we registered ourselves, in
/// the shapes agents actually namespace MCP tools with.
///
/// Both of those can still miss: ACP's `ToolCall` carries no tool-name field at
/// all, so an adapter that reports only a human title matches neither. The
/// third check closes what it safely can: Lemma publishes the exact tool names
/// it serves on this run, so a *structured* name field that is one of those is
/// ours.
///
/// What none of them do any more is trust the tool call's `title`. It is free
/// text the agent writes, and matching a scoped-tool name against it approved
/// anything merely *called* `lemma_…` without showing a card or recording an
/// event. See `permission_tool_candidates`. The cost of that fix is that an
/// agent supplying no tool name and no `_meta` now raises an approval card per
/// Lemma tool call; the alternative was a permission system anything could talk
/// its way past.
pub(crate) fn is_scoped_mcp_tool_approval(
    request: &RequestPermissionRequest,
    scoped_tools: &HashSet<String>,
) -> bool {
    let Ok(value) = serde_json::to_value(request) else {
        return false;
    };
    let flagged = value
        .get("_meta")
        .and_then(|meta| meta.get("is_mcp_tool_approval"))
        .and_then(Value::as_bool)
        .unwrap_or(false);
    flagged
        || names_scoped_mcp_tool(&value)
        || names_known_scoped_tool(&value, scoped_tools)
        || title_names_a_published_tool(&value, scoped_tools)
}

/// The tool names Lemma said it serves on this run's MCP endpoint.
///
/// Absent for an older control plane that does not publish them, in which case
/// this check simply contributes nothing and the other two still apply.
pub(crate) fn scoped_mcp_tool_names(mcp: &Value) -> HashSet<String> {
    mcp.get("tool_names")
        .and_then(Value::as_array)
        .map(|names| {
            names
                .iter()
                .filter_map(Value::as_str)
                .map(|name| name.trim().to_ascii_lowercase())
                .filter(|name| !name.is_empty())
                .collect()
        })
        .unwrap_or_default()
}

/// Does the request name a tool Lemma told us it serves?
///
/// Exact match against the published set, after stripping whatever namespacing
/// the agent added, so this cannot sweep in a same-named tool of the user's.
pub(crate) fn names_known_scoped_tool(value: &Value, scoped_tools: &HashSet<String>) -> bool {
    if scoped_tools.is_empty() {
        return false;
    }
    permission_tool_candidates(value).any(|name| {
        let name = name.trim().to_ascii_lowercase();
        let bare = scoped_tool_name(&name).map(str::to_ascii_lowercase);
        scoped_tools.contains(name.as_str())
            || bare.is_some_and(|bare| scoped_tools.contains(bare.as_str()))
    })
}

/// Every field of a permission request that might carry the tool's identity.
///
/// `_meta` is included because it is where an adapter puts the real name when
/// the title is written for a human: Claude Code reports
/// `_meta.claudeCode.toolName`, and this reads any vendor's key rather than
/// that one convention.
pub(crate) fn permission_tool_candidates(value: &Value) -> impl Iterator<Item = &str> {
    // Deliberately NOT `/toolCall/title` or `/toolCall/toolCallId`.
    //
    // Both are free text the *agent* writes -- `title` is explicitly a human
    // label, and the id is whatever the adapter felt like generating. Matching
    // a scoped-tool name against either meant any permission request whose
    // title merely began `lemma_`, `lemma.`, `lemma/` or `lemma:` was approved
    // silently: no card shown, and nothing written to the transcript, because
    // this check runs before the prompt is raised and before the event is
    // recorded.
    //
    // That is reachable two ways. Prompt injection in anything the agent reads
    // -- a repo file, a web page, an issue -- saying "name this step
    // lemma_build" converts an unapproved shell command into a silent one. And
    // it fires by accident: an agent working in this very repository could
    // reasonably title a step "lemma/desktop: run cargo test".
    //
    // What is left is what the model cannot author for itself: the structured
    // tool-name fields an adapter fills in, and `_meta`. An agent that supplies
    // none of them now prompts, which is the correct default -- an extra
    // approval card costs a click, and this cost the permission system.
    [
        value.pointer("/toolCall/toolName").and_then(Value::as_str),
        value.pointer("/toolCall/name").and_then(Value::as_str),
        value.get("toolName").and_then(Value::as_str),
    ]
    .into_iter()
    .flatten()
    .chain(meta_tool_names(value.pointer("/toolCall/_meta")))
    .chain(meta_tool_names(value.get("_meta")))
}

/// Is the *title* naming one of our tools, in the one shape where that is safe?
///
/// `permission_tool_candidates` excludes the title on purpose: matching a
/// namespace prefix against free text the agent writes approved anything merely
/// called `lemma_...`. This is the narrow case where the title is not free text
/// at all, and all three conditions have to hold.
///
/// It exists because of what the real adapters send. Claude Code's ACP adapter
/// routes every MCP tool through the default arm of `toolInfoFromToolUse`,
/// which returns `{ title: name, kind: "other" }` -- the raw namespaced tool
/// name, in the title field. It emits no `toolCall.toolName`, no
/// `toolCall.name`, and sets `_meta.claudeCode.toolName` only when the call
/// carries a `parentToolUseId`, i.e. only inside a sub-agent. So for an ordinary
/// top-level `mcp__lemma_tools__lemma_read_table` the title is the only field
/// carrying the identity, and ignoring it made every Lemma tool call raise an
/// approval card -- which is not a permission system, it is a way to train
/// people to click through one.
///
/// 1. **The kind is `other`.** This is the discriminator that makes the rest
///    safe, and the model does not choose it: the adapter derives `kind` from
///    which tool was called. `Bash` is `execute`, `Read` is `read`, `Task` is
///    `think`; only an MCP or unknown tool is `other`. So a shell command
///    cannot reach this path however it is titled -- which matters most for an
///    adapter that titles a command with its *description* rather than the
///    command itself.
/// 2. **The title carries our namespace.** A bare `lemma_read_table` is a claim
///    anyone can make; `mcp__lemma_tools__lemma_read_table` is the form an
///    adapter produces from a real registration.
/// 3. **The bare name is one this run published.** Exact, against `tool_names`
///    from `START_RUN`. This is what leaves no room for a payload: an
///    injection
///    has to append something, and any appended character breaks the equality.
pub(crate) fn title_names_a_published_tool(value: &Value, scoped_tools: &HashSet<String>) -> bool {
    if scoped_tools.is_empty() {
        return false;
    }
    let kind = value
        .pointer("/toolCall/kind")
        .or_else(|| value.get("kind"))
        .and_then(Value::as_str);
    // Absent is not `other`. An adapter that declines to classify its tools
    // tells us nothing, and this path is only safe because the classification
    // is the adapter's rather than the model's.
    if kind != Some("other") {
        return false;
    }
    [
        value.pointer("/toolCall/title").and_then(Value::as_str),
        value.get("title").and_then(Value::as_str),
    ]
    .into_iter()
    .flatten()
    .any(|title| {
        scoped_tool_name(title)
            .map(str::to_ascii_lowercase)
            .is_some_and(|bare| scoped_tools.contains(bare.as_str()))
    })
}

/// `_meta.<vendor>.toolName`, for whatever vendor keys the payload carries.
pub(crate) fn meta_tool_names(meta: Option<&Value>) -> impl Iterator<Item = &str> {
    meta.and_then(Value::as_object)
        .into_iter()
        .flat_map(|meta| meta.values())
        .filter_map(|vendor| vendor.get("toolName").and_then(Value::as_str))
}

/// The always-allow this request offers, named by the scope it would grant.
///
/// The agent writes that name from the permission rules it would install — the
/// difference between "Always Allow all Bash" and "Always Allow
/// WebFetch(domain:github.com)" is the whole grant — so it is both what the
/// user is shown and what a later request has to match to be covered by it.
pub(crate) fn always_allow_offer(request: &RequestPermissionRequest) -> Option<AlwaysAllowOffer> {
    request
        .options
        .iter()
        .find(|option| option.kind == PermissionOptionKind::AllowAlways)
        .map(|option| AlwaysAllowOffer {
            scope: AlwaysAllowScope {
                session_id: request.session_id.to_string(),
                label: option.name.trim().to_owned(),
            },
            option_id: option.option_id.to_string(),
        })
}

/// The names Lemma's run-scoped MCP server is known by.
///
/// `lemma_tools` is the one every run publishes and the one the server is
/// registered under. The others are names Lemma itself shipped earlier — a
/// hyphenated config, and the bare `lemma` this host used before it registered
/// the run's published name — and are still in stored conversations. Longest
/// first, so `lemma_tools` is read as itself and not as `lemma` plus `_tools`.
pub(crate) const SCOPED_MCP_SERVER_NAMES: [&str; 3] =
    ["lemma_tools", "lemma-tools", SCOPED_MCP_SERVER];

/// How an agent marks a name as coming from an MCP server at all.
pub(crate) const MCP_MARKERS: [&str; 3] = ["mcp__", "mcp.", "mcp/"];

/// What may join a server name to a tool name. No `-`: `lemma-tools` is a
/// server name in its own right above, and treating `-` as a separator would
/// read someone else's `lemma-corp` server as ours.
pub(crate) const NAMESPACE_SEPARATORS: [char; 4] = ['_', '.', '/', ':'];

/// Does the tool being requested belong to Lemma's own MCP server?
///
/// Agents namespace MCP tools differently — `mcp__lemma_tools__read_table`,
/// `lemma_tools.read_table`, `lemma/read_table` — so this looks for one of our
/// server names followed by a separator rather than assuming a convention.
/// Anchored at the start, and matched whole: a user's own tool that merely
/// mentions Lemma, or their own server called `lemma-corp`, is theirs to
/// approve.
pub(crate) fn names_scoped_mcp_tool(value: &Value) -> bool {
    permission_tool_candidates(value).any(|name| scoped_tool_name(name).is_some())
}

/// The tool's own name with our namespace removed, or `None` if the name is
/// not one of ours.
pub(crate) fn scoped_tool_name(name: &str) -> Option<&str> {
    let name = name.trim();
    // `to_ascii_lowercase` is length-preserving, so offsets taken from the
    // lowercased copy index the original correctly.
    let lowered = name.to_ascii_lowercase();
    let without_marker = MCP_MARKERS
        .iter()
        .find(|marker| lowered.starts_with(**marker))
        .map_or(name, |marker| &name[marker.len()..]);
    let lowered = without_marker.to_ascii_lowercase();
    SCOPED_MCP_SERVER_NAMES.iter().find_map(|server| {
        let rest = lowered.strip_prefix(server)?;
        rest.starts_with(NAMESPACE_SEPARATORS).then(|| {
            without_marker[without_marker.len() - rest.len()..]
                .trim_start_matches(NAMESPACE_SEPARATORS)
        })
    })
}

pub(crate) fn tool_call_id(payload: &JsonMap) -> Option<String> {
    payload
        .get("toolCall")
        .and_then(Value::as_object)
        .and_then(|object| find_string(object, &["toolCallId", "id"]))
}
