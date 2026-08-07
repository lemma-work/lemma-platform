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
