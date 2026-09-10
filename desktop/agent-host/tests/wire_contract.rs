//! The Rust half of the wire contract in `fixtures/wire_contract.json`.
//!
//! `lemma-backend` asserts the same file from Python. Neither side can move
//! alone: the enum has to name the same events, and the two text extractors
//! have to agree character for character, because the host accumulates streamed
//! text with one and the backend re-accumulates it with the other. The run
//! spec is the third: both sides declare its fields, and only the fixture says
//! which of them the backend adds as it hands the command over.

use std::collections::BTreeSet;
use std::path::PathBuf;

use lemma_agent_host::protocol::{EventType, JsonMap, RunSpec};
use serde_json::Value;
use uuid::Uuid;

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

/// Bounds the backend enforces, which this host must not exceed.
///
/// Exceeding one is not a graceful degradation. An `object_id` over the column
/// length gets its whole batch refused, which the host reads as the run's fault
/// and answers by discarding the transcript; a `max_runs` over the cap makes
/// every poll 422, so the host reports itself offline indefinitely. Both were
/// unbounded here, and neither limit was written down anywhere both sides read.
#[test]
fn the_host_respects_the_bounds_the_backend_enforces() {
    let contract = contract();
    let limits = &contract["limits"];

    assert_eq!(
        limits["max_runs"].as_u64(),
        Some(u64::from(lemma_agent_host::config::MAX_SUPPORTED_RUNS)),
        "the configured capacity ceiling must match what the backend accepts"
    );
    assert_eq!(
        limits["object_id_max_length"].as_u64(),
        Some(255),
        "object_id is truncated to this in acp.rs; both sides read it here"
    );
}

/// The protocol version both sides send and compare, from one place.
///
/// `lemma_agent_host::PROTOCOL_VERSION` and the backend's
/// `AGENT_HOST_PROTOCOL_VERSION` are two literals in two languages. The host
/// puts its number in every identity it publishes and the backend checks it;
/// raising one without the other makes every host of the old version look
/// unrecognised, from the moment the backend deploys, with nothing failing on
/// either side to say so.
#[test]
fn the_protocol_version_is_the_one_the_backend_expects() {
    let declared = contract()["protocol_version"]
        .as_u64()
        .expect("the contract declares a protocol version");
    assert_eq!(
        u64::from(lemma_agent_host::PROTOCOL_VERSION),
        declared,
        "PROTOCOL_VERSION and the shared contract disagree; the backend reads \
         the contract's number, so raise both or neither",
    );
}

/// The two `RunSpec` declarations, which only one side states in full.
///
/// The `START_RUN` payload is a run spec in two languages. Every field but one
/// is declared on both; `mcp` is declared only here, because on the backend it
/// rests in the command row as `encrypted_mcp` and is decrypted into `mcp` by
/// `AgentHostDispatchRepository._wire_command` on the way out. A Python model
/// field would be somewhere for a run-scoped credential to sit in plaintext,
/// so its absence is deliberate -- and was indistinguishable from drift until
/// this recorded which fields each side is supposed to have.
///
/// This half can only assert the union: `mcp` is a field of this struct whether
/// the contract calls it shared or added on delivery. Which side of that line a
/// field falls on is the Python half's to check, because it is the one that can
/// see the model `mcp` is deliberately missing from.
#[test]
fn the_run_spec_carries_the_fields_the_contract_names() {
    let contract = contract();
    let run_spec = &contract["run_spec"];

    let shared = run_spec["fields"]
        .as_array()
        .expect("run_spec.fields is a list")
        .iter()
        .map(|value| value.as_str().expect("a field name is a string").to_owned())
        .collect::<BTreeSet<_>>();
    let on_delivery = run_spec["added_on_delivery"]
        .as_object()
        .expect("run_spec.added_on_delivery is an object")
        .keys()
        .cloned()
        .collect::<BTreeSet<_>>();

    let ours = serde_json::to_value(sample_run_spec())
        .expect("a run spec serializes")
        .as_object()
        .expect("as an object")
        .keys()
        .cloned()
        .collect::<BTreeSet<_>>();

    assert_eq!(
        ours,
        shared.union(&on_delivery).cloned().collect::<BTreeSet<_>>(),
        "this side's run spec and the contract name different fields"
    );
    assert!(
        shared.is_disjoint(&on_delivery),
        "a field cannot be both declared on both sides and added on delivery"
    );
}

/// Every field set, so serialization cannot omit one and pass.
fn sample_run_spec() -> RunSpec {
    RunSpec {
        agent_run_id: Uuid::nil(),
        conversation_id: Uuid::nil(),
        harness_id: Uuid::nil(),
        profile_revision: "r1".to_owned(),
        model_name: Some("m".to_owned()),
        config_selections: JsonMap::new(),
        system_prompt: "s".to_owned(),
        prompt: vec![serde_json::json!({"type": "text", "text": "hello"})],
        resume_session_id: Some("session".to_owned()),
        workspace_cwd: Some("project".to_owned()),
        context: JsonMap::new(),
        mcp: serde_json::json!({}),
        run_deadline: chrono::Utc::now(),
        system_prompt_delivery: Some("NEW_SESSION_ONLY".to_owned()),
    }
}
