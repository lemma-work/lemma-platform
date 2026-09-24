//! Real adapter sessions, replayed through the normalizers.
//!
//! Each `tests/fixtures/acp/<adapter>@<version>/<prompt>.jsonl` is a session
//! recorded from the pinned adapter by `record_transcript.py`; the matching
//! `<prompt>.expected.json` is what the normalizer made of it, reviewed and
//! checked in. A change to a normalizer shows up here as a diff of exactly what
//! a person would see in a conversation.
//!
//! Re-record with `record_transcript.py` and regenerate with
//! `UPDATE_GOLDEN=1 cargo test -p lemma-agent-host --test normalize_golden`.
//! See "Golden transcripts" in docs/architecture/agent-host-events.md.

use std::collections::BTreeSet;
use std::path::{Path, PathBuf};

use agent_client_protocol::schema::v1::SessionUpdate;
use lemma_agent_host::normalize::{Dialect, Normalized, Normalizer, RunContext};
use serde_json::{Value, json};

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/acp")
}

/// What a recorded run's MCP configuration would have told the host.
fn context() -> RunContext {
    RunContext {
        lemma_server: Some("lemma_tools".into()),
        lemma_tools: ["lemma_exec_command", "lemma_display_resource"]
            .into_iter()
            .map(str::to_owned)
            .collect(),
    }
}

fn dialect_for(directory: &Path) -> Dialect {
    let name = directory.file_name().unwrap().to_string_lossy();
    let adapter = name.split('@').next().unwrap();
    Dialect::for_harness(adapter)
}

fn event_json(event: &Normalized) -> Value {
    json!({
        "type": event.event_type,
        "object_id": event.object_id,
        "payload": event.payload,
    })
}

/// Replay one transcript exactly as the driver would see it: every update is
/// parsed into ACP's own type first, so a field the SDK drops is dropped here
/// too.
fn replay(dialect: Dialect, transcript: &Path) -> Vec<Value> {
    let mut normalizer = Normalizer::new(dialect, context());
    let mut out = Vec::new();
    for line in std::fs::read_to_string(transcript).unwrap().lines() {
        let record: Value = serde_json::from_str(line).unwrap();
        let Some(message) = record.get("message") else {
            continue;
        };
        if record["direction"] != "agent" {
            continue;
        }
        match message.get("method").and_then(Value::as_str) {
            Some("session/update") => {
                let update: SessionUpdate =
                    serde_json::from_value(message["params"]["update"].clone())
                        .unwrap_or_else(|error| panic!("{line}\n{error}"));
                out.extend(normalizer.session_update(&update).iter().map(event_json));
            }
            Some("session/request_permission") => {
                let (events, request_id, fields) =
                    normalizer.permission_request(&message["params"]);
                out.extend(events.iter().map(event_json));
                out.push(json!({
                    "type": "permission_request",
                    "object_id": request_id,
                    "payload": fields,
                }));
            }
            // The answer to `session/prompt`: the turn is over.
            None if message
                .get("result")
                .and_then(|result| result.get("stopReason"))
                .is_some() =>
            {
                let usage = message["result"].get("usage");
                out.extend(normalizer.finish(usage).iter().map(event_json));
            }
            _ => {}
        }
    }
    out
}

#[test]
fn every_recorded_transcript_normalizes_as_reviewed() {
    let update = std::env::var_os("UPDATE_GOLDEN").is_some();
    let mut checked = 0;
    let mut failures = Vec::new();
    for directory in std::fs::read_dir(fixtures()).unwrap() {
        let directory = directory.unwrap().path();
        if !directory.is_dir() {
            continue;
        }
        let dialect = dialect_for(&directory);
        for transcript in std::fs::read_dir(&directory).unwrap() {
            let transcript = transcript.unwrap().path();
            if transcript.extension().and_then(|ext| ext.to_str()) != Some("jsonl") {
                continue;
            }
            let expected_path = transcript.with_extension("expected.json");
            let actual = Value::Array(replay(dialect, &transcript));
            if update {
                let mut text = serde_json::to_string_pretty(&actual).unwrap();
                text.push('\n');
                std::fs::write(&expected_path, text).unwrap();
            } else {
                let expected: Value = serde_json::from_str(
                    &std::fs::read_to_string(&expected_path).unwrap_or_else(|_| {
                        panic!(
                            "{} has no reviewed expectation; run with UPDATE_GOLDEN=1 and review it",
                            transcript.display()
                        )
                    }),
                )
                .unwrap();
                if expected != actual {
                    failures.push(transcript.display().to_string());
                }
            }
            checked += 1;
        }
    }
    assert!(checked > 0, "no transcripts were found");
    assert!(
        failures.is_empty(),
        "these transcripts no longer normalize as reviewed (rerun with UPDATE_GOLDEN=1 and review the diff): {failures:?}"
    );
}

/// Every tool call in every transcript is paired with exactly one result,
/// opened before it, under one id -- whatever the adapter did.
#[test]
fn every_call_is_announced_once_and_closed_once() {
    for directory in std::fs::read_dir(fixtures()).unwrap() {
        let directory = directory.unwrap().path();
        if !directory.is_dir() {
            continue;
        }
        for transcript in std::fs::read_dir(&directory).unwrap() {
            let transcript = transcript.unwrap().path();
            if transcript.extension().and_then(|ext| ext.to_str()) != Some("jsonl") {
                continue;
            }
            let events = replay(dialect_for(&directory), &transcript);
            let mut opened = BTreeSet::new();
            let mut closed = BTreeSet::new();
            for event in &events {
                let id = event["object_id"].as_str().unwrap_or_default().to_owned();
                match event["type"].as_str() {
                    Some("tool_call") => {
                        assert!(
                            opened.insert(id.clone()),
                            "{id} opened twice in {transcript:?}"
                        );
                    }
                    Some("tool_call_result") => {
                        assert!(
                            opened.contains(&id),
                            "{id} closed before it opened in {transcript:?}"
                        );
                        assert!(
                            closed.insert(id.clone()),
                            "{id} closed twice in {transcript:?}"
                        );
                    }
                    _ => {}
                }
            }
            assert_eq!(opened, closed, "a call was never closed in {transcript:?}");
            // And no call is left named after a guess.
            for event in events.iter().filter(|event| event["type"] == "tool_call") {
                let name = event["payload"]["tool"]["name"].as_str().unwrap();
                assert_ne!(name, "tool", "an unnamed call in {transcript:?}: {event}");
            }
        }
    }
}

/// Bumping an adapter in the lock file means recording it again: a normalizer
/// written against one version's shapes says nothing about the next.
#[test]
fn every_pinned_adapter_has_a_transcript_or_says_why_not() {
    let lock: Value = serde_json::from_str(
        &std::fs::read_to_string(
            Path::new(env!("CARGO_MANIFEST_DIR")).join("agent-adapters.lock.json"),
        )
        .unwrap(),
    )
    .unwrap();
    let unrecorded: Value =
        serde_json::from_str(&std::fs::read_to_string(fixtures().join("unrecorded.json")).unwrap())
            .unwrap();
    for adapter in lock["adapters"].as_array().unwrap() {
        let key = adapter["key"].as_str().unwrap();
        let version = adapter["adapter_version"].as_str().unwrap();
        let directory = fixtures().join(format!("{key}@{version}"));
        let has_transcript = std::fs::read_dir(&directory).is_ok_and(|entries| {
            entries
                .filter_map(Result::ok)
                .any(|entry| entry.path().extension().and_then(|ext| ext.to_str()) == Some("jsonl"))
        });
        let excused = unrecorded
            .get(format!("{key}@{version}"))
            .and_then(Value::as_str)
            .is_some_and(|reason| !reason.trim().is_empty());
        assert!(
            has_transcript || excused,
            "{key}@{version} is pinned but has no recorded transcript in {}, and no reason in \
             unrecorded.json; record one with record_transcript.py",
            directory.display()
        );
    }
}
