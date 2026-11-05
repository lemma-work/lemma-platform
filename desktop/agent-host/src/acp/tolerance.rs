//! Reading what an agent sends without breaking when the agent changes.
//!
//! The adapters this host drives are released weekly and the protocol crate
//! models ACP as it stood when it was pinned. Most of that crate is already
//! forgiving -- lists skip an entry they cannot read, optional fields default
//! on error -- but a few places are not, and each one turned "the agent added
//! something" into "the run lost something it needed":
//!
//! - a `session/update` with a `sessionUpdate` kind the crate does not know
//!   failed to parse, and the crate's own warning logged the whole update,
//!   content and all;
//! - a message chunk carrying a content block type it does not know, the same;
//! - one permission option with a new `kind` failed the whole
//!   `session/request_permission`, which the agent then saw as an error;
//! - a plan entry with a new priority or status was dropped from the plan.
//!
//! This is the first handler on the connection. It looks at the raw JSON
//! before anything typed does, takes out or settles what is unknown, says so
//! once per kind (the kind's name only, never the content), and hands the rest
//! on. What is unknown is skipped, never guessed at -- and never, for a
//! permission, read as consent.
//!
//! See "Resilience to agent updates" in docs/architecture/agent-host.md.

use std::collections::HashSet;
use std::future::Future;
use std::sync::{Mutex, OnceLock};

use agent_client_protocol::schema::v1::{RequestPermissionRequest, SessionNotification};
use agent_client_protocol::{Agent, ConnectionTo, Dispatch, HandleDispatchFrom, Handled};
use serde_json::{Map, Value, json};

/// The `session/update` kinds this host's protocol crate parses.
const UPDATE_KINDS: [&str; 11] = [
    "user_message_chunk",
    "agent_message_chunk",
    "agent_thought_chunk",
    "tool_call",
    "tool_call_update",
    "plan",
    "available_commands_update",
    "current_mode_update",
    "config_option_update",
    "session_info_update",
    "usage_update",
];
const CONTENT_BLOCKS: [&str; 5] = ["text", "image", "audio", "resource_link", "resource"];
const TOOL_CONTENT: [&str; 3] = ["content", "diff", "terminal"];
const TOOL_KINDS: [&str; 10] = [
    "read",
    "edit",
    "delete",
    "move",
    "search",
    "execute",
    "think",
    "fetch",
    "switch_mode",
    "other",
];
const TOOL_STATUSES: [&str; 4] = ["pending", "in_progress", "completed", "failed"];
const PLAN_PRIORITIES: [&str; 3] = ["high", "medium", "low"];
const PLAN_STATUSES: [&str; 3] = ["pending", "in_progress", "completed"];
const PERMISSION_KINDS: [&str; 4] = ["allow_once", "allow_always", "reject_once", "reject_always"];
/// The stop reasons `outcome_for` speaks to.
pub(crate) const STOP_REASONS: [&str; 5] = [
    "end_turn",
    "max_tokens",
    "max_turn_requests",
    "refusal",
    "cancelled",
];

/// Say, once per process and kind, that the agent sent something this host
/// does not know. Later sightings go to debug, so an adapter that sends a new
/// update on every token cannot flood the log.
///
/// `kind` is the agent's identifier for the thing, never its content, and is
/// trimmed to a short run of identifier characters before it is logged.
pub(crate) fn note_unknown(what: &'static str, kind: &str) {
    const REMEMBERED: usize = 256;
    static SEEN: OnceLock<Mutex<HashSet<(&'static str, String)>>> = OnceLock::new();
    let kind: String = kind
        .chars()
        .filter(|character| character.is_ascii_alphanumeric() || "_-./:".contains(*character))
        .take(64)
        .collect();
    let first = SEEN
        .get_or_init(Mutex::default)
        .lock()
        .is_ok_and(|mut seen| seen.len() < REMEMBERED && seen.insert((what, kind.clone())));
    if first {
        tracing::warn!(
            what,
            kind = %kind,
            "the agent sent something this Agent Host does not know; skipping it"
        );
    } else {
        tracing::debug!(what, kind = %kind, "skipping an unknown agent value again");
    }
}

fn type_of(value: &Value, key: &str) -> Option<String> {
    value.get(key).and_then(Value::as_str).map(str::to_owned)
}

/// Settle a raw `session/update`'s params in place. `false` means skip it.
///
/// Whatever passes has been parsed as a `SessionNotification` once already, so
/// the typed handler after this one cannot fail on it.
#[must_use]
pub fn sanitize_session_update(params: &mut Value) -> bool {
    let Some(update) = params.get_mut("update").and_then(Value::as_object_mut) else {
        note_unknown("session/update", "<no update>");
        return false;
    };
    let kind = update
        .get("sessionUpdate")
        .and_then(Value::as_str)
        .unwrap_or("<none>")
        .to_owned();
    if !UPDATE_KINDS.contains(&kind.as_str()) {
        note_unknown("session/update kind", &kind);
        return false;
    }
    let keep = match kind.as_str() {
        "user_message_chunk" | "agent_message_chunk" | "agent_thought_chunk" => {
            update.get("content").is_some_and(known_content_block)
        }
        "tool_call" | "tool_call_update" => {
            settle_tool_call(update);
            true
        }
        "plan" => {
            settle_plan(update);
            true
        }
        "usage_update" => {
            for key in ["used", "size"] {
                whole_number(update, key);
            }
            true
        }
        _ => true,
    };
    if !keep {
        return false;
    }
    if serde_json::from_value::<SessionNotification>(params.clone()).is_err() {
        note_unknown("session/update shape", &kind);
        return false;
    }
    true
}

/// One `session/update`, as the typed notification the driver forwards, or
/// `None` when it is something to skip.
#[must_use]
pub fn session_notification(mut params: Value) -> Option<SessionNotification> {
    sanitize_session_update(&mut params)
        .then(|| serde_json::from_value(params).ok())
        .flatten()
}

/// Whether a content block is one of the types ACP defines.
fn known_content_block(block: &Value) -> bool {
    let kind = type_of(block, "type").unwrap_or_else(|| "<none>".to_owned());
    let known = CONTENT_BLOCKS.contains(&kind.as_str());
    if !known {
        note_unknown("content block", &kind);
    }
    known
}

/// A tool call's report, with what this host cannot read taken out of it.
///
/// An unknown `kind` is left for the protocol crate, which reads it as
/// `other` -- a display hint here. (A permission request is different; see
/// `sanitize_permission_request`.) An unknown status is removed: the call
/// stays open until a status this host knows closes it, or the turn ends.
fn settle_tool_call(call: &mut Map<String, Value>) {
    if let Some(kind) = call.get("kind").and_then(Value::as_str)
        && !TOOL_KINDS.contains(&kind)
    {
        note_unknown("tool call kind", kind);
    }
    if let Some(status) = call.get("status").and_then(Value::as_str)
        && !TOOL_STATUSES.contains(&status)
    {
        note_unknown("tool call status", status);
        call.remove("status");
    }
    if let Some(Value::Array(content)) = call.get_mut("content") {
        content.retain(|item| {
            let kind = type_of(item, "type").unwrap_or_else(|| "<none>".to_owned());
            if !TOOL_CONTENT.contains(&kind.as_str()) {
                note_unknown("tool call content", &kind);
                return false;
            }
            kind != "content" || item.get("content").is_some_and(known_content_block)
        });
    }
}

/// A plan, with a priority or status this host does not know read as the
/// plainest one, so the entry stays on the plan instead of vanishing from it.
fn settle_plan(plan: &mut Map<String, Value>) {
    let Some(Value::Array(entries)) = plan.get_mut("entries") else {
        return;
    };
    for entry in entries.iter_mut().filter_map(Value::as_object_mut) {
        for (key, known, plainest) in [
            ("priority", &PLAN_PRIORITIES[..], "medium"),
            ("status", &PLAN_STATUSES[..], "pending"),
        ] {
            let value = entry.get(key).and_then(Value::as_str);
            if value.is_some_and(|value| known.contains(&value)) {
                continue;
            }
            note_unknown(
                if key == "priority" {
                    "plan entry priority"
                } else {
                    "plan entry status"
                },
                value.unwrap_or("<none>"),
            );
            entry.insert(key.to_owned(), Value::from(plainest));
        }
    }
}

/// A count an adapter sent as `12.0` or `"12"`, as the whole number ACP's
/// type requires; `None` for anything that is not a count.
#[must_use]
pub fn whole_count(value: &Value) -> Option<u64> {
    if let Some(count) = value.as_u64() {
        return Some(count);
    }
    let number = value
        .as_f64()
        .or_else(|| value.as_str().and_then(|text| text.trim().parse().ok()))
        .filter(|number: &f64| number.is_finite() && *number >= 0.0)?;
    // Saturating, and a count is never fractional in a way that matters.
    #[allow(clippy::cast_possible_truncation, clippy::cast_sign_loss)]
    Some(number.round() as u64)
}

fn whole_number(object: &mut Map<String, Value>, key: &str) {
    if let Some(count) = object.get(key).and_then(whole_count) {
        object.insert(key.to_owned(), Value::from(count));
    }
}

/// Settle a raw `session/request_permission`'s params in place. `false` means
/// this host cannot offer any of the agent's options, and the request is to be
/// answered `cancelled`.
///
/// An option of a kind this host does not know is *removed*, never mapped to a
/// known one: nothing about a new kind's name says whether it allows, so
/// selecting it on anyone's behalf -- or showing it as a choice -- would be a
/// guess about consent. The known options remain, so a request that adds a new
/// kind beside the four ACP names still works as before.
///
/// An unknown tool `kind` is removed as well, rather than left for the crate
/// to read as `other`. `title_names_a_published_tool` trusts `other` precisely
/// because an adapter, not the model, chooses it; a kind no adapter of ours has
/// sent before is not something to extend that trust to. Absent is not
/// `other`, so the request takes the ordinary approval path.
#[must_use]
pub fn sanitize_permission_request(params: &mut Value) -> bool {
    if let Some(Value::Array(options)) = params.get_mut("options") {
        options.retain(|option| {
            let kind = type_of(option, "kind").unwrap_or_else(|| "<none>".to_owned());
            let known = PERMISSION_KINDS.contains(&kind.as_str());
            if !known {
                note_unknown("permission option kind", &kind);
            }
            known
        });
        if options.is_empty() {
            return false;
        }
    }
    if let Some(call) = params.get_mut("toolCall").and_then(Value::as_object_mut) {
        if let Some(kind) = call.get("kind").and_then(Value::as_str)
            && !TOOL_KINDS.contains(&kind)
        {
            note_unknown("permission tool kind", kind);
            call.remove("kind");
        }
        settle_tool_call(call);
    }
    if serde_json::from_value::<RequestPermissionRequest>(params.clone()).is_err() {
        note_unknown("permission request shape", "session/request_permission");
        return false;
    }
    true
}

/// The answer to a permission request this host cannot read: `cancelled`,
/// which is ACP's "no" and the narrowest thing to say.
#[must_use]
pub fn cancelled_permission() -> Value {
    json!({ "outcome": { "outcome": "cancelled" } })
}

/// The first handler on an agent connection. See the module docs.
#[derive(Clone, Copy, Debug, Default)]
pub(crate) struct Tolerance;

impl Tolerance {
    fn settle(message: Dispatch) -> Result<Handled<Dispatch>, agent_client_protocol::Error> {
        match message {
            Dispatch::Notification(mut notification) if notification.method == "session/update" => {
                if !sanitize_session_update(&mut notification.params) {
                    return Ok(Handled::Yes);
                }
                Ok(Handled::No {
                    message: Dispatch::Notification(notification),
                    retry: false,
                })
            }
            Dispatch::Request(mut request, responder)
                if request.method == "session/request_permission" =>
            {
                if !sanitize_permission_request(&mut request.params) {
                    responder.respond(cancelled_permission())?;
                    return Ok(Handled::Yes);
                }
                Ok(Handled::No {
                    message: Dispatch::Request(request, responder),
                    retry: false,
                })
            }
            other => Ok(Handled::No {
                message: other,
                retry: false,
            }),
        }
    }
}

impl HandleDispatchFrom<Agent> for Tolerance {
    fn handle_dispatch_from(
        &mut self,
        message: Dispatch,
        _connection: ConnectionTo<Agent>,
    ) -> impl Future<Output = Result<Handled<Dispatch>, agent_client_protocol::Error>> + Send {
        std::future::ready(Self::settle(message))
    }

    fn describe_chain(&self) -> impl std::fmt::Debug {
        "tolerance"
    }
}
