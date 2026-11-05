//! The recorded transcripts, replayed as a newer agent would send them.
//!
//! Adapters change weekly. The rule this holds the host to (see "Resilience to
//! agent updates" in docs/architecture/agent-host.md): whatever an agent adds
//! -- a field, a `session/update` kind, a tool-call kind, a content block, a
//! permission option kind, a configuration option type, a `_meta` key -- is
//! skipped, and the parts this host knows still make exactly the events they
//! made before. So every transcript is replayed twice, once as recorded and
//! once with the future injected at every level, through the same tolerance
//! layer the driver puts first on the connection, and the two must match the
//! reviewed `<prompt>.expected.json`.

use std::path::{Path, PathBuf};

use agent_client_protocol::schema::v1::{NewSessionResponse, RequestPermissionRequest};
use lemma_agent_host::acp::{sanitize_permission_request, session_notification};
use lemma_agent_host::normalize::{Dialect, Normalized, Normalizer, RunContext};
use serde_json::{Map, Value, json};

fn fixtures() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/fixtures/acp")
}

fn context() -> RunContext {
    RunContext {
        lemma_server: Some("lemma_tools".into()),
        lemma_tools: ["lemma_exec_command", "lemma_display_resource"]
            .into_iter()
            .map(str::to_owned)
            .collect(),
    }
}

fn event_json(event: &Normalized) -> Value {
    json!({
        "type": event.event_type,
        "object_id": event.object_id,
        "payload": event.payload,
    })
}

/// Every transcript, with the adapter it was recorded from.
fn transcripts() -> Vec<(Dialect, PathBuf)> {
    let mut found = Vec::new();
    for directory in std::fs::read_dir(fixtures()).unwrap() {
        let directory = directory.unwrap().path();
        if !directory.is_dir() {
            continue;
        }
        let name = directory.file_name().unwrap().to_string_lossy().to_string();
        let dialect = Dialect::for_harness(name.split('@').next().unwrap());
        for transcript in std::fs::read_dir(&directory).unwrap() {
            let transcript = transcript.unwrap().path();
            if transcript.extension().and_then(|ext| ext.to_str()) == Some("jsonl") {
                found.push((dialect, transcript));
            }
        }
    }
    assert!(!found.is_empty(), "no transcripts were found");
    found
}

/// The agent's messages in a transcript, in order.
fn agent_messages(transcript: &Path) -> Vec<Value> {
    std::fs::read_to_string(transcript)
        .unwrap()
        .lines()
        .map(|line| serde_json::from_str::<Value>(line).unwrap())
        .filter(|record| record["direction"] == "agent")
        .filter_map(|record| record.get("message").cloned())
        .collect()
}

/// Replay messages the way the driver reads them: through the tolerance
/// layer, then ACP's own types, then the normalizer.
fn replay(dialect: Dialect, messages: &[Value]) -> Vec<Value> {
    let mut normalizer = Normalizer::new(dialect, context());
    let mut out = Vec::new();
    for message in messages {
        match message.get("method").and_then(Value::as_str) {
            Some("session/update") => {
                if let Some(notification) = session_notification(message["params"].clone()) {
                    out.extend(
                        normalizer
                            .session_update(&notification.update)
                            .iter()
                            .map(event_json),
                    );
                }
            }
            Some("session/request_permission") => {
                let mut params = message["params"].clone();
                assert!(
                    sanitize_permission_request(&mut params),
                    "a permission request with known options was refused"
                );
                // As the driver hands it on: parsed into ACP's type, so a field
                // the protocol crate drops is dropped here too.
                let request: RequestPermissionRequest = serde_json::from_value(params).unwrap();
                let (events, request_id, fields) =
                    normalizer.permission_request(&serde_json::to_value(&request).unwrap());
                out.extend(events.iter().map(event_json));
                out.push(json!({
                    "type": "permission_request",
                    "object_id": request_id,
                    "payload": fields,
                }));
            }
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

/// Free-form values the agent fills in for its tools: what is inside them is
/// carried through as data, so a field injected there is a different input,
/// not a new protocol field.
const OPAQUE: [&str; 4] = ["rawInput", "rawOutput", "_meta", "text"];

/// Add a field nobody has heard of to every object, at every level.
fn add_unknown_fields(value: &mut Value) {
    match value {
        Value::Object(object) => {
            for (key, child) in object.iter_mut() {
                if !OPAQUE.contains(&key.as_str()) {
                    add_unknown_fields(child);
                }
            }
            object.insert(
                "lemmaFutureField".to_owned(),
                json!({ "nested": [1, "two", { "three": null }] }),
            );
        }
        Value::Array(items) => items.iter_mut().for_each(add_unknown_fields),
        _ => {}
    }
}

/// A `_meta` key from a vendor nobody has heard of, beside whatever is there.
fn add_unknown_meta(object: &mut Map<String, Value>) {
    let meta = object
        .entry("_meta")
        .or_insert_with(|| Value::Object(Map::new()));
    if let Some(meta) = meta.as_object_mut() {
        meta.insert(
            "futureVendor".to_owned(),
            json!({ "traceId": "abc", "flags": ["x"] }),
        );
    }
}

fn update(session_id: &Value, update: &Value) -> Value {
    json!({
        "jsonrpc": "2.0",
        "method": "session/update",
        "params": { "sessionId": session_id, "update": update },
    })
}

/// The same transcript, as an agent from the future would send it.
fn from_the_future(messages: &[Value]) -> Vec<Value> {
    let mut future = Vec::new();
    for message in messages {
        let mut message = message.clone();
        let method = message
            .get("method")
            .and_then(Value::as_str)
            .map(str::to_owned);
        let session_id = message
            .pointer("/params/sessionId")
            .cloned()
            .unwrap_or(Value::Null);
        if let Some(params) = message.get_mut("params") {
            add_unknown_fields(params);
            if let Some(params) = params.as_object_mut() {
                add_unknown_meta(params);
            }
            if let Some(update) = params.get_mut("update").and_then(Value::as_object_mut) {
                add_unknown_meta(update);
                // A content block type and a tool-content type nobody has
                // heard of, beside the ones the call already had.
                if let Some(Value::Array(content)) = update.get_mut("content") {
                    content.push(json!({ "type": "hologram", "data": "..." }));
                    content.push(json!({
                        "type": "content",
                        "content": { "type": "hologram", "data": "..." },
                    }));
                }
            }
            // A permission option kind nobody has heard of. It must be
            // removed, not offered, and above all never chosen as "allow".
            if method.as_deref() == Some("session/request_permission")
                && let Some(Value::Array(options)) = params.get_mut("options")
            {
                options.insert(
                    0,
                    json!({
                        "optionId": "allow_for_the_session",
                        "name": "Allow for this session",
                        "kind": "allow_session",
                    }),
                );
            }
        }
        if let Some(result) = message.get_mut("result").and_then(Value::as_object_mut) {
            add_unknown_meta(result);
            result.insert("lemmaFutureField".to_owned(), json!(true));
        }
        let is_update = method.as_deref() == Some("session/update");
        future.push(message);
        if is_update {
            // Kinds of update nobody has heard of, after every known one.
            future.push(update(
                &session_id,
                &json!({ "sessionUpdate": "agent_reflection", "content": { "type": "text", "text": "x" } }),
            ));
            future.push(update(
                &session_id,
                &json!({ "sessionUpdate": "agent_message_chunk", "content": { "type": "hologram", "data": "x" } }),
            ));
            future.push(update(
                &session_id,
                &json!({ "sessionUpdate": "plan_update", "planId": "p", "entries": [] }),
            ));
        }
    }
    future
}

fn expected(transcript: &Path) -> Value {
    serde_json::from_str(
        &std::fs::read_to_string(transcript.with_extension("expected.json")).unwrap(),
    )
    .unwrap()
}

#[test]
fn the_tolerance_layer_changes_nothing_about_a_recorded_transcript() {
    for (dialect, transcript) in transcripts() {
        let actual = Value::Array(replay(dialect, &agent_messages(&transcript)));
        assert_eq!(
            actual,
            expected(&transcript),
            "{} normalizes differently through the tolerance layer",
            transcript.display()
        );
    }
}

#[test]
fn a_newer_agent_still_normalizes_as_reviewed() {
    for (dialect, transcript) in transcripts() {
        let future = from_the_future(&agent_messages(&transcript));
        let actual = Value::Array(replay(dialect, &future));
        assert_eq!(
            actual,
            expected(&transcript),
            "{} normalizes differently once a newer agent adds to it",
            transcript.display()
        );
    }
}

/// The session the agent opens, with a configuration option of a type no
/// version of ACP this host knows defines, still opens -- with every option it
/// knows, and without the one it does not.
#[test]
fn a_new_configuration_option_type_is_not_offered_and_breaks_nothing() {
    for (_, transcript) in transcripts() {
        for message in agent_messages(&transcript) {
            let Some(result) = message.get("result") else {
                continue;
            };
            if result.get("sessionId").is_none() {
                continue;
            }
            let recorded: NewSessionResponse = serde_json::from_value(result.clone()).unwrap();
            let mut future = result.clone();
            add_unknown_fields(&mut future);
            let options = future
                .as_object_mut()
                .unwrap()
                .entry("configOptions")
                .or_insert_with(|| json!([]));
            options.as_array_mut().unwrap().push(json!({
                "id": "temperature",
                "name": "Temperature",
                "category": "_vendor_sampling",
                "type": "number",
                "currentValue": 0.2,
                "minimum": 0,
                "maximum": 1,
            }));
            let parsed: NewSessionResponse = serde_json::from_value(future)
                .unwrap_or_else(|error| panic!("{}: {error}", transcript.display()));
            assert_eq!(parsed.session_id, recorded.session_id);
            let ids = |response: &NewSessionResponse| {
                response
                    .config_options
                    .iter()
                    .flatten()
                    .map(|option| option.id.to_string())
                    .collect::<Vec<_>>()
            };
            assert_eq!(ids(&parsed), ids(&recorded), "{}", transcript.display());
        }
    }
}
