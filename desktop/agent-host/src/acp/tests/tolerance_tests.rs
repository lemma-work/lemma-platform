//! What a newer agent may send, and what this host makes of it. See
//! `tolerance`, and `tests/future_agent.rs` for the recorded transcripts.

use super::*;
use crate::normalize::{Dialect, Normalizer, RunContext};
use crate::permissions::PermissionDecision;
use agent_client_protocol::schema::v1::{PermissionOption, SessionConfigOption, SessionUpdate};
use serde_json::json;

fn params(update: Value) -> Value {
    let mut params = serde_json::Map::new();
    params.insert("sessionId".to_owned(), Value::from("s"));
    params.insert("update".to_owned(), update);
    Value::Object(params)
}

#[test]
fn an_unknown_update_kind_is_skipped_not_failed() {
    let mut raw = params(json!({ "sessionUpdate": "agent_reflection", "text": "secret" }));
    assert!(!sanitize_session_update(&mut raw));
    assert!(session_notification(raw).is_none());
}

#[test]
fn a_chunk_with_an_unknown_content_block_is_skipped() {
    let raw = params(json!({
        "sessionUpdate": "agent_message_chunk",
        "content": { "type": "hologram", "data": "..." },
    }));
    assert!(session_notification(raw).is_none());
}

#[test]
fn unknown_fields_and_meta_do_not_stop_a_chunk() {
    let raw = params(json!({
        "sessionUpdate": "agent_message_chunk",
        "content": { "type": "text", "text": "hello", "fresh": 1 },
        "messageId": "m1",
        "_meta": { "futureVendor": { "x": 1 } },
        "brandNew": [1, 2],
    }));
    let notification = session_notification(raw).expect("a known chunk");
    let events = Normalizer::new(Dialect::Generic, RunContext::default())
        .session_update(&notification.update);
    assert_eq!(events.len(), 1);
    assert_eq!(events[0].payload["text"], "hello");
}

#[test]
fn a_tool_call_of_an_unknown_kind_is_still_a_tool_call() {
    let opened = session_notification(params(json!({
        "sessionUpdate": "tool_call",
        "toolCallId": "c1",
        "title": "Look at the page",
        "kind": "browse",
        "status": "pending",
        "rawInput": { "url": "https://example.com" },
        "content": [{ "type": "hologram" }],
    })))
    .expect("an unknown kind is a display hint, not a reason to drop the call");
    // An unknown status does not close the call; a known one later does.
    let unknown_status = session_notification(params(json!({
        "sessionUpdate": "tool_call_update",
        "toolCallId": "c1",
        "status": "paused",
    })))
    .expect("an unknown status is removed, not fatal");
    let closed = session_notification(params(json!({
        "sessionUpdate": "tool_call_update",
        "toolCallId": "c1",
        "status": "completed",
        "rawOutput": "done",
    })))
    .unwrap();
    let mut normalizer = Normalizer::new(Dialect::Generic, RunContext::default());
    let mut events = Vec::new();
    for notification in [opened, unknown_status, closed] {
        events.extend(normalizer.session_update(&notification.update));
    }
    let kinds: Vec<_> = events.iter().map(|event| event.event_type).collect();
    assert_eq!(kinds, [EventType::ToolCall, EventType::ToolCallResult]);
    // ACP's `other` is its default, and a default is not sent: the card
    // carries no kind rather than one this host made up.
    assert!(events[0].payload["tool"]["kind"].is_null());
}

#[test]
fn a_plan_entry_with_a_new_priority_or_status_stays_on_the_plan() {
    let notification = session_notification(params(json!({
        "sessionUpdate": "plan",
        "entries": [
            { "content": "one", "priority": "urgent", "status": "blocked" },
            { "content": "two", "priority": "low", "status": "completed" },
        ],
    })))
    .unwrap();
    let SessionUpdate::Plan(plan) = notification.update else {
        panic!("a plan");
    };
    assert_eq!(plan.entries.len(), 2);
}

#[test]
fn a_usage_update_with_fractional_counts_is_still_read() {
    let notification = session_notification(params(json!({
        "sessionUpdate": "usage_update",
        "used": 1200.0,
        "size": "200000",
        "cost": { "amount": 0.1, "currency": "USD", "breakdown": {} },
    })))
    .expect("whole numbers sent as floats or strings");
    let events = Normalizer::new(Dialect::Generic, RunContext::default())
        .session_update(&notification.update);
    assert_eq!(events[0].payload["context"]["used"], 1200);
    assert_eq!(events[0].payload["context"]["size"], 200_000);
}

fn permission(options: &Value, kind: &str) -> Value {
    json!({
        "sessionId": "s",
        "toolCall": {
            "toolCallId": "c1",
            "title": "mcp__lemma_tools__lemma_exec_command",
            "kind": kind,
        },
        "options": options,
        "_meta": { "futureVendor": {} },
        "brandNew": true,
    })
}

fn request(raw: Value) -> RequestPermissionRequest {
    serde_json::from_value(raw).unwrap()
}

#[test]
fn an_unknown_permission_option_is_removed_and_never_selected() {
    let mut raw = permission(
        &json!([
            { "optionId": "forever", "name": "Allow for the session", "kind": "allow_session" },
            { "optionId": "no", "name": "Reject", "kind": "reject_once" },
        ]),
        "execute",
    );
    assert!(sanitize_permission_request(&mut raw));
    let request = request(raw);
    assert_eq!(request.options.len(), 1);
    // With no allow option left, neither the narrowest allow nor a person's
    // "yes" can select the unknown one: both come back as cancelled.
    assert_eq!(
        allow_once(&request.options),
        RequestPermissionOutcome::Cancelled
    );
    assert_eq!(
        outcome_for_decision(
            PermissionDecision::Allow {
                option_id: "forever".to_owned()
            },
            &request.options
        ),
        RequestPermissionOutcome::Cancelled
    );
}

#[test]
fn a_request_offering_only_unknown_options_is_cancelled() {
    let mut raw = permission(
        &json!([{ "optionId": "x", "name": "Sure", "kind": "allow_forever" }]),
        "execute",
    );
    assert!(!sanitize_permission_request(&mut raw));
    assert_eq!(
        cancelled_permission(),
        serde_json::to_value(RequestPermissionResponse::new(
            RequestPermissionOutcome::Cancelled
        ))
        .unwrap()
    );
}

#[test]
fn known_permission_options_beside_an_unknown_one_still_work() {
    let mut raw = permission(
        &json!([
            { "optionId": "once", "name": "Allow", "kind": "allow_once" },
            { "optionId": "s", "name": "Session", "kind": "allow_session" },
            { "optionId": "no", "name": "Reject", "kind": "reject_once" },
        ]),
        "execute",
    );
    assert!(sanitize_permission_request(&mut raw));
    let request = request(raw);
    let RequestPermissionOutcome::Selected(selected) = allow_once(&request.options) else {
        panic!("the known allow option");
    };
    assert_eq!(selected.option_id.to_string(), "once");
}

/// The crate reads an unknown tool kind as `other`, and `other` is what lets a
/// permission's title name one of Lemma's tools. A kind no adapter has sent
/// before does not get that trust.
#[test]
fn an_unknown_tool_kind_on_a_permission_is_not_read_as_other() {
    let scoped: HashSet<String> = std::iter::once("lemma_exec_command".to_owned()).collect();
    let options = json!([{ "optionId": "once", "name": "Allow", "kind": "allow_once" }]);
    let mut known = permission(&options, "other");
    assert!(sanitize_permission_request(&mut known));
    assert!(is_scoped_mcp_tool_approval(&request(known), &scoped));

    let mut unknown = permission(&options, "browse");
    // Untouched, the crate would have said `other`.
    assert!(is_scoped_mcp_tool_approval(
        &request(unknown.clone()),
        &scoped
    ));
    assert!(sanitize_permission_request(&mut unknown));
    assert!(!is_scoped_mcp_tool_approval(&request(unknown), &scoped));
}

#[test]
fn an_unknown_stop_reason_ends_the_turn_named_not_in_a_parse_error() {
    let (stop_reason, usage) = prompt_response(&json!({
        "stopReason": "budget_exhausted",
        "usage": { "inputTokens": 10, "outputTokens": 2.0, "totalTokens": 12, "new": "x" },
        "_meta": { "futureVendor": {} },
    }));
    assert_eq!(stop_reason, "budget_exhausted");
    let (state, message) = run_outcome(false, &stop_reason);
    assert_eq!(state, RunState::Failed);
    assert!(message.unwrap().contains("budget_exhausted"));
    let usage = usage.expect("the adapter's usage object");
    let owed = Normalizer::new(Dialect::Generic, RunContext::default()).finish(Some(&usage));
    assert_eq!(owed[0].payload["output_tokens"], 2);
    assert_eq!(owed[0].payload["input_tokens"], 10);

    let (known, _) = prompt_response(&json!({ "stopReason": "end_turn" }));
    assert_eq!(run_outcome(false, &known).0, RunState::Succeeded);
}

#[test]
fn a_configuration_option_of_a_new_type_is_not_offered() {
    let response: agent_client_protocol::schema::v1::SetSessionConfigOptionResponse =
        serde_json::from_value(json!({
            "configOptions": [
                { "id": "temperature", "name": "Temperature", "type": "number", "currentValue": 0.2 },
                {
                    "id": "model", "name": "Model", "category": "model", "type": "select",
                    "currentValue": "a", "options": [{ "value": "a", "name": "A", "fresh": 1 }],
                    "brandNew": {},
                },
            ],
        }))
        .unwrap();
    let ids: Vec<_> = response
        .config_options
        .iter()
        .filter_map(|option| convert_config_option("codex", option))
        .map(|option| option.id)
        .collect();
    assert_eq!(ids, ["model"]);
}

/// ACP lets an agent group a select's choices. Each grouped choice is offered
/// on its own, so membership -- and the policy filter -- can see it.
#[test]
fn grouped_choices_are_offered_one_by_one() {
    let option: SessionConfigOption = serde_json::from_value(json!({
        "id": "mode",
        "name": "Mode",
        "category": "mode",
        "type": "select",
        "currentValue": "default",
        "options": [
            { "group": "safe", "name": "Safe", "options": [
                { "value": "default", "name": "Default" },
                { "value": "plan", "name": "Plan" },
            ]},
            { "group": "unsafe", "name": "Unsafe", "options": [
                { "value": "bypassPermissions", "name": "Bypass" },
            ]},
        ],
    }))
    .unwrap();
    let converted = convert_config_option("claude-code", &option).unwrap();
    let values: Vec<_> = converted.options.iter().filter_map(option_value).collect();
    assert_eq!(values, [&json!("default"), &json!("plan")]);
    assert_eq!(converted.options[0]["group"], "Safe");
    assert!(!selection_is_allowed(
        &converted,
        &json!("bypassPermissions")
    ));
    assert!(!selection_is_allowed(&converted, &json!("anything")));
    assert!(selection_is_allowed(&converted, &json!("plan")));
}

#[test]
fn unknown_permission_options_leave_no_trace_in_the_card() {
    let mut raw = permission(
        &json!([
            { "optionId": "once", "name": "Allow", "kind": "allow_once" },
            { "optionId": "forever", "name": "Forever", "kind": "allow_session" },
        ]),
        "execute",
    );
    assert!(sanitize_permission_request(&mut raw));
    let payload = permission_payload(&request(raw));
    let options: Vec<PermissionOption> =
        serde_json::from_value(payload["options"].clone()).unwrap();
    assert_eq!(options.len(), 1);
}
