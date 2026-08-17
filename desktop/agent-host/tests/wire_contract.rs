//! The Rust half of the wire contract in `fixtures/wire_contract.json`.
//!
//! `lemma-backend` asserts the same file from Python. Neither side can move
//! alone: the enum has to name the same events, and the two text extractors
//! have to agree character for character, because the host accumulates streamed
//! text with one and the backend re-accumulates it with the other.

use std::collections::BTreeSet;
use std::path::PathBuf;

use lemma_agent_host::protocol::EventType;
use serde_json::Value;

fn contract() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/wire_contract.json");
    let raw = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("could not read {}: {error}", path.display()));
    serde_json::from_str(&raw).expect("the wire contract is valid JSON")
}

/// Every `EventType` this host can emit, as it appears on the wire.
fn every_event_type() -> Vec<EventType> {
    vec![
        EventType::RunState,
        EventType::UserMessage,
        EventType::AgentMessageChunk,
        EventType::AgentMessageUpsert,
        EventType::AgentThoughtChunk,
        EventType::AgentThoughtUpsert,
        EventType::PlanUpsert,
        EventType::ToolCallUpsert,
        EventType::ToolCallUpdate,
        EventType::UsageUpdate,
        EventType::ConfigUpdate,
        EventType::PermissionRequest,
        EventType::Terminal,
    ]
}

#[test]
fn the_event_type_enum_matches_the_contract() {
    let declared = contract()["event_types"]
        .as_array()
        .expect("event_types is a list")
        .iter()
        .map(|value| {
            value
                .as_str()
                .expect("an event type is a string")
                .to_owned()
        })
        .collect::<BTreeSet<_>>();

    let ours = every_event_type()
        .into_iter()
        .map(|event_type| {
            serde_json::to_value(event_type)
                .expect("an event type serializes")
                .as_str()
                .expect("as a string")
                .to_owned()
        })
        .collect::<BTreeSet<_>>();

    assert_eq!(
        ours, declared,
        "an event one side emits and the other does not know is an event that \
         reaches the backend and is dropped"
    );
}

/// Guards `every_event_type` against a variant added without being listed —
/// which would let a new event pass this file without either side agreeing.
#[test]
fn every_event_type_is_exhaustive() {
    fn assert_covered(event_type: EventType) {
        match event_type {
            EventType::RunState
            | EventType::UserMessage
            | EventType::AgentMessageChunk
            | EventType::AgentMessageUpsert
            | EventType::AgentThoughtChunk
            | EventType::AgentThoughtUpsert
            | EventType::PlanUpsert
            | EventType::ToolCallUpsert
            | EventType::ToolCallUpdate
            | EventType::UsageUpdate
            | EventType::ConfigUpdate
            | EventType::PermissionRequest
            | EventType::Terminal => {}
        }
    }
    for event_type in every_event_type() {
        assert_covered(event_type);
    }
}

/// The host must hand every field of a tool-call update through untouched.
///
/// This side owns only half the promise — the backend reads the arguments and
/// the result back out of what lands here, and asserts that half against the
/// same fixture. What the host has to guarantee is that nothing is dropped on
/// the way: `rawInput` on a refining update is the only place a streamed call's
/// arguments ever appear, so an update this normalizer declined to forward
/// would leave them unrecoverable no matter what the backend did.
#[test]
fn tool_call_updates_survive_normalization() {
    for case in contract()["tool_calls"]
        .as_array()
        .expect("tool_calls is a list")
    {
        let name = case["name"].as_str().unwrap_or("unnamed");
        for update in case["updates"].as_array().expect("updates is a list") {
            let parsed = serde_json::from_value(update.clone())
                .unwrap_or_else(|error| panic!("case {name:?}: unparseable update: {error}"));
            let (_, object_id, payload) = lemma_agent_host::acp::normalize_session_update(&parsed)
                .unwrap_or_else(|| panic!("case {name:?}: {update} was dropped"));

            assert_eq!(
                object_id.as_deref(),
                update["toolCallId"].as_str(),
                "case {name:?}: the call's id did not survive"
            );
            for field in ["rawInput", "rawOutput", "title"] {
                let Some(expected) = update.get(field) else {
                    continue;
                };
                assert_eq!(
                    payload.get(field),
                    Some(expected),
                    "case {name:?}: {field} did not survive normalization"
                );
            }
            // `status` is the exception, and only in one direction. ACP makes
            // `pending` the default and serde skips defaults, so an opening
            // call arrives with no status at all — which is fine, because
            // "pending" tells the backend nothing it does not already know from
            // the call opening. A *terminal* status is the opposite: it is the
            // only signal that the call is closed and its result is final, so
            // losing one would leave the call open forever and the run would
            // synthesize a return saying it never finished.
            if let Some(status) = update["status"].as_str()
                && status != "pending"
            {
                assert_eq!(
                    payload.get("status").and_then(Value::as_str),
                    Some(status),
                    "case {name:?}: a terminal status did not survive normalization"
                );
            }
        }
    }
}

#[test]
fn chunk_text_matches_the_contract() {
    for case in contract()["text_extraction"]
        .as_array()
        .expect("text_extraction is a list")
    {
        let name = case["name"].as_str().unwrap_or("unnamed");
        let payload = case["payload"]
            .as_object()
            .expect("a case payload is an object")
            .iter()
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect();
        let expected = case["text"].as_str().expect("a case text is a string");

        assert_eq!(
            lemma_agent_host::runtime::chunk_text(&payload),
            expected,
            "case {name:?} disagrees with the shared contract"
        );
    }
}
