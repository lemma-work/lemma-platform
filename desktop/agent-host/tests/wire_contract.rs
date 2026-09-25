//! The Rust half of the wire contract in `fixtures/wire_contract.json`.
//!
//! `lemma-backend` asserts the same file from Python. Neither side can move
//! alone: the two text extractors have to agree character for character,
//! because the host accumulates streamed text with one and the backend
//! re-accumulates it with the other; the tool events the backend turns into
//! conversation messages have to be ones this host sends; and the run spec
//! has to carry the same fields on both sides, with only the fixture saying
//! which of them the backend adds as it hands the command over.

use std::collections::BTreeSet;
use std::path::PathBuf;

use lemma_agent_host::protocol::{EventType, JsonMap, RunSpec, ToolCallPayload, ToolResultPayload};
use serde_json::Value;
use uuid::Uuid;

fn contract() -> Value {
    let path = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/wire_contract.json");
    let raw = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("could not read {}: {error}", path.display()));
    serde_json::from_str(&raw).expect("the wire contract is valid JSON")
}

// The event types, the wire enums and the link's frame vocabulary and close
// codes are held to this same file by unit tests next to their declarations:
// `protocol::tests::the_wire_enums_match_the_contract_exactly` and
// `link::protocol::tests`. They read the names from serde and from the
// declarations themselves, which a list written out here could only repeat.

/// Tool events as the contract pins them are events this host can have sent.
///
/// The fixture's `tool_events` are the backend's half: it asserts the
/// conversation message it makes of each. That is only worth anything while
/// those events are ones this side produces, so each payload has to read as
/// the host's own type and write back out unchanged -- a field renamed,
/// dropped or added on either side fails here rather than as a tool call the
/// backend silently cannot read. Whether the host produces them from real
/// adapter output is the golden transcripts' job (`normalize_golden.rs`).
#[test]
fn tool_events_are_ones_the_host_emits() {
    for case in contract()["tool_events"]
        .as_array()
        .expect("tool_events is a list")
    {
        let name = case["name"].as_str().unwrap_or("unnamed");
        let events = case["events"].as_array().expect("events is a list");
        let mut opened = None;
        for event in events {
            let event_type: EventType = serde_json::from_value(event["type"].clone())
                .unwrap_or_else(|error| panic!("case {name:?}: unknown event type: {error}"));
            let object_id = event["object_id"]
                .as_str()
                .unwrap_or_else(|| panic!("case {name:?}: a tool event names its call"));
            assert!(
                object_id.len() <= 255,
                "case {name:?}: object_id exceeds the column the backend stores it in"
            );
            let payload = &event["payload"];
            let round_trip = match event_type {
                EventType::ToolCall => {
                    let call: ToolCallPayload = serde_json::from_value(payload.clone())
                        .unwrap_or_else(|error| {
                            panic!("case {name:?}: not a tool_call payload: {error}")
                        });
                    opened = Some(object_id.to_owned());
                    serde_json::to_value(call).unwrap()
                }
                EventType::ToolCallResult => {
                    assert_eq!(
                        opened.as_deref(),
                        Some(object_id),
                        "case {name:?}: a result follows the call it closes"
                    );
                    let result: ToolResultPayload = serde_json::from_value(payload.clone())
                        .unwrap_or_else(|error| {
                            panic!("case {name:?}: not a tool_call_result payload: {error}")
                        });
                    serde_json::to_value(result).unwrap()
                }
                EventType::ToolCallProgress => payload.clone(),
                other => panic!("case {name:?}: {other:?} is not a tool event"),
            };
            assert_eq!(
                &round_trip, payload,
                "case {name:?}: the host would not send this payload as written"
            );
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
/// and answers by discarding the transcript; a `max_runs` over the cap made
/// every poll 422, and now fails every frame that reports the host's capacity,
/// so the host reports itself offline indefinitely. Both were
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
