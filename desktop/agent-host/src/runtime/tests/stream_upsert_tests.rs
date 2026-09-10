use std::sync::Arc;
use std::sync::atomic::AtomicBool;

use super::{JournalCallbacks, StreamSegments, chunk_text};
use crate::acp::AcpCallbacks;
use crate::journal::Journal;
use crate::protocol::{Command, CommandKind, EventType, JsonMap, RunSpec};
use chrono::Utc;
use serde_json::Value;
use tempfile::TempDir;
use uuid::Uuid;

/// A long answer that only ever streams text never changes kind, so nothing
/// sealed its segment: the whole thing sat in memory, was written again in
/// full as a single upsert row, and reached Lemma twice. Sealing on size as
/// well as on a change of kind bounds it, and `recover_event` already
/// replays multiple upserts correctly.
#[test]
fn a_long_text_only_answer_is_sealed_in_pieces_rather_than_held_whole() {
    let (_directory, callbacks, run_id) = fixture();

    // Comfortably past the seal threshold, in realistic chunk sizes.
    let chunk = "a".repeat(4096);
    for _ in 0..40 {
        callbacks
            .event(EventType::AgentMessageChunk, None, payload(&chunk))
            .unwrap();
    }

    let batches = callbacks
        .journal
        .pending_events(callbacks.target_id, 4096)
        .unwrap();
    let upserts = batches
        .iter()
        .flat_map(|batch| batch.events.iter())
        .filter(|event| event.event_type == EventType::AgentMessageUpsert)
        .count();
    assert!(
        upserts >= 2,
        "a {}KiB answer should seal more than once, saw {upserts}",
        40 * 4
    );

    let recovered: String = batches
        .iter()
        .flat_map(|batch| batch.events.iter())
        .filter(|event| event.event_type == EventType::AgentMessageChunk)
        .map(|event| super::chunk_text(&event.payload))
        .collect();
    assert_eq!(
        recovered.len(),
        40 * 4096,
        "sealing must not drop or duplicate any of the answer"
    );
    let _ = run_id;
}

/// A run whose model the harness will not take reports that as a config
/// update *before* the prompt goes out. Lemma treats a RUNNING checkpoint
/// as proof the prompt landed and promotes the conversation's pending
/// instructions to delivered on it -- so that pre-dispatch event marked
/// them delivered before a prompt existed, and a run that then died before
/// dispatch left them skipped for the rest of the conversation.
#[test]
fn an_event_before_dispatch_does_not_claim_the_prompt_landed() {
    let (_directory, callbacks, run_id) = fixture();
    // The fixture stands in for a run already under way; this one has not
    // dispatched yet.
    callbacks
        .dispatched
        .store(false, std::sync::atomic::Ordering::SeqCst);
    callbacks
        .provider_seen
        .store(false, std::sync::atomic::Ordering::SeqCst);

    callbacks
        .event(EventType::ConfigUpdate, None, payload("model_unavailable"))
        .unwrap();

    let run = callbacks
        .journal
        .get_run(callbacks.target_id, run_id)
        .unwrap()
        .unwrap();
    assert_ne!(
        run.state,
        crate::protocol::RunState::Running,
        "a config update before the prompt must not report the run as running"
    );
    assert!(
        !run.prompt_dispatched,
        "and must not look like a prompt that landed"
    );
}

fn payload(text: &str) -> JsonMap {
    let mut payload = JsonMap::new();
    payload.insert("text".to_owned(), Value::String(text.to_owned()));
    payload
}

fn fixture() -> (TempDir, JournalCallbacks, Uuid) {
    let directory = TempDir::new().unwrap();
    let journal = Journal::open(directory.path().join("journal.db")).unwrap();
    let target_id = Uuid::new_v4();
    let run_id = Uuid::new_v4();
    let spec = RunSpec {
        agent_run_id: run_id,
        conversation_id: Uuid::new_v4(),
        harness_id: Uuid::new_v4(),
        profile_revision: "revision".into(),
        model_name: None,
        config_selections: JsonMap::new(),
        system_prompt: String::new(),
        prompt: vec![serde_json::json!({"type": "text", "text": "hi"})],
        resume_session_id: None,
        workspace_cwd: None,
        context: JsonMap::new(),
        mcp: Value::Null,
        run_deadline: Utc::now() + chrono::Duration::minutes(5),
        system_prompt_delivery: None,
    };
    let command = Command {
        command_id: Uuid::new_v4(),
        kind: CommandKind::StartRun,
        created_at: Utc::now(),
        expires_at: Utc::now() + chrono::Duration::minutes(1),
        run_id: Some(run_id),
        lease_epoch: Some(1),
        payload: serde_json::to_value(&spec).unwrap(),
    };
    journal
        .accept_start(target_id, &command, &spec, "codex", "1.0")
        .unwrap();
    let callbacks = JournalCallbacks {
        journal,
        target_id,
        run_id,
        lease_epoch: 1,
        host_cwd: Some("/test/Projects/Δ workspace".to_owned()),
        provider_seen: AtomicBool::new(true),
        dispatched: AtomicBool::new(true),
        stream_segments: std::sync::Mutex::new(StreamSegments::default()),
        events_ready: Arc::new(tokio::sync::Notify::new()),
    };
    (directory, callbacks, run_id)
}

fn journaled_events(callbacks: &JournalCallbacks) -> Vec<(u64, EventType, JsonMap)> {
    callbacks
        .journal
        .pending_events(callbacks.target_id, 256)
        .unwrap()
        .into_iter()
        .flat_map(|batch| batch.events)
        .map(|event| (event.sequence, event.event_type, event.payload))
        .collect()
}

#[test]
fn session_binding_is_durable_before_any_answer_or_control_poll() {
    let (directory, callbacks, _) = fixture();
    callbacks.before_prompt("claude-session-42").unwrap();
    let established = journaled_events(&callbacks);
    assert_eq!(established.len(), 1);
    assert_eq!(established[0].1, EventType::RunState);
    assert_eq!(established[0].2["state"], "DISPATCHING");
    assert_eq!(established[0].2["provider_session_id"], "claude-session-42");
    assert_eq!(established[0].2["host_cwd"], "/test/Projects/Δ workspace");
    callbacks
        .event(EventType::AgentMessageChunk, None, payload("answer"))
        .unwrap();
    callbacks.flush_stream_segments().unwrap();
    let reopened = Journal::open(directory.path().join("journal.db")).unwrap();
    let batch = reopened.pending_events(callbacks.target_id, 256).unwrap();
    let events = &batch[0].events;
    assert_eq!(
        events[0].payload["provider_session_id"],
        "claude-session-42"
    );
    assert!(
        events
            .iter()
            .skip(1)
            .any(|event| event.event_type == EventType::AgentMessageUpsert)
    );
    assert!(
        events
            .windows(2)
            .all(|pair| pair[0].sequence < pair[1].sequence)
    );
}

#[test]
fn only_the_exact_streamed_failure_can_be_replaced_by_an_error_hint() {
    for (streamed, error, matches) in [
        (
            "Failed to authenticate",
            "Internal error: Failed to authenticate",
            true,
        ),
        ("Failed to authenticate", "Failed to authenticate", true),
        (
            "Here is the partial answer",
            "Internal error: connection closed",
            false,
        ),
        (
            "Useful work. Failed to authenticate",
            "Internal error: Failed to authenticate",
            false,
        ),
        ("", "Internal error: Failed to authenticate", false),
        ("", "", false),
    ] {
        let (_directory, callbacks, _run_id) = fixture();
        for character in streamed.chars() {
            callbacks
                .event(
                    EventType::AgentMessageChunk,
                    None,
                    payload(&character.to_string()),
                )
                .unwrap();
        }
        callbacks.flush_stream_segments().unwrap();
        assert_eq!(
            callbacks.stream_matches_failure(error).unwrap(),
            matches,
            "{streamed:?}, {error:?}"
        );
    }
}

#[test]
fn text_chunks_flush_as_one_upsert_before_the_next_durable_event() {
    let (_directory, callbacks, _run_id) = fixture();
    let callbacks = Arc::new(callbacks);
    callbacks
        .event(EventType::AgentMessageChunk, None, payload("hello "))
        .unwrap();
    callbacks
        .event(EventType::AgentMessageChunk, None, payload("world"))
        .unwrap();
    // Only chunks are journaled so far; no upsert yet.
    let events = journaled_events(&callbacks);
    assert_eq!(
        events.iter().map(|(_, kind, _)| *kind).collect::<Vec<_>>(),
        vec![EventType::AgentMessageChunk, EventType::AgentMessageChunk]
    );

    callbacks
        .event(
            EventType::ToolCallUpsert,
            Some("call-1".into()),
            JsonMap::new(),
        )
        .unwrap();
    let events = journaled_events(&callbacks);
    let kinds = events.iter().map(|(_, kind, _)| *kind).collect::<Vec<_>>();
    assert_eq!(
        kinds,
        vec![
            EventType::AgentMessageChunk,
            EventType::AgentMessageChunk,
            EventType::AgentMessageUpsert,
            EventType::ToolCallUpsert,
        ]
    );
    assert_eq!(
        events[2].2.get("text"),
        Some(&Value::String("hello world".into()))
    );

    // A durable-only replay (skipping chunks) still yields the full text.
    let durable_text = events
        .iter()
        .filter(|(_, kind, _)| *kind == EventType::AgentMessageUpsert)
        .filter_map(|(_, _, payload)| payload.get("text").and_then(Value::as_str))
        .collect::<String>();
    assert_eq!(durable_text, "hello world");
}

#[test]
fn recovery_seals_only_text_after_each_kinds_last_upsert() {
    let (_directory, callbacks, run_id) = fixture();
    callbacks
        .event(EventType::AgentMessageChunk, None, payload("already saved"))
        .unwrap();
    callbacks
        .event(
            EventType::ToolCallUpsert,
            Some("tool".into()),
            JsonMap::new(),
        )
        .unwrap();
    callbacks
        .event(
            EventType::AgentThoughtChunk,
            None,
            payload("unfinished thought"),
        )
        .unwrap();
    callbacks
        .event(
            EventType::AgentMessageChunk,
            None,
            payload("unfinished answer"),
        )
        .unwrap();
    let last_sequence = journaled_events(&callbacks).last().unwrap().0;
    callbacks
        .journal
        .acknowledge_events(
            callbacks.target_id,
            &crate::protocol::EventAck {
                run_id,
                lease_epoch: 1,
                acked_through: last_sequence,
            },
        )
        .unwrap();
    assert!(journaled_events(&callbacks).is_empty());
    let mut recovered = StreamSegments::default();
    callbacks
        .journal
        .visit_run_events(callbacks.target_id, run_id, 1, |event| {
            recovered.recover_event(&event);
        })
        .unwrap();
    assert_eq!(recovered.message, "unfinished answer");
    assert_eq!(recovered.thought, "unfinished thought");
    let mut another_lease_events = 0;
    callbacks
        .journal
        .visit_run_events(callbacks.target_id, run_id, 2, |_| {
            another_lease_events += 1;
        })
        .unwrap();
    assert_eq!(another_lease_events, 0);
}

#[test]
fn rich_content_seals_the_segment_without_touching_it() {
    let (_directory, callbacks, _run_id) = fixture();
    callbacks
        .event(EventType::AgentMessageChunk, None, payload("before "))
        .unwrap();
    let mut image = JsonMap::new();
    image.insert(
        "content".to_owned(),
        serde_json::json!({"type": "image", "data": "...", "mimeType": "image/png"}),
    );
    callbacks
        .event(EventType::AgentMessageChunk, None, image)
        .unwrap();
    callbacks
        .event(EventType::AgentMessageChunk, None, payload("after"))
        .unwrap();
    callbacks
        .event(EventType::Terminal, None, JsonMap::new())
        .unwrap();

    let upserts = journaled_events(&callbacks)
        .into_iter()
        .filter(|(_, kind, _)| *kind == EventType::AgentMessageUpsert)
        .map(|(_, _, payload)| payload)
        .collect::<Vec<_>>();
    assert_eq!(
        upserts
            .iter()
            .filter_map(|payload| payload.get("text").and_then(Value::as_str))
            .collect::<Vec<_>>(),
        vec!["before ", "after"]
    );
}

#[test]
fn thought_and_message_segments_flush_independently() {
    let (_directory, callbacks, _run_id) = fixture();
    callbacks
        .event(EventType::AgentThoughtChunk, None, payload("thinking"))
        .unwrap();
    callbacks
        .event(EventType::AgentMessageChunk, None, payload("answer"))
        .unwrap();
    callbacks
        .event(EventType::Terminal, None, JsonMap::new())
        .unwrap();
    let events = journaled_events(&callbacks);
    let kinds = events.iter().map(|(_, kind, _)| *kind).collect::<Vec<_>>();
    assert_eq!(
        kinds,
        vec![
            EventType::AgentThoughtChunk,
            EventType::AgentMessageChunk,
            EventType::AgentMessageUpsert,
            EventType::AgentThoughtUpsert,
            EventType::Terminal,
        ]
    );
}

#[test]
fn chunk_text_mirrors_the_backend_extraction() {
    assert_eq!(chunk_text(&payload("plain")), "plain");
    let mut content = JsonMap::new();
    content.insert(
        "content".to_owned(),
        serde_json::json!({"type": "text", "text": "block"}),
    );
    assert_eq!(chunk_text(&content), "block");
    let mut image = JsonMap::new();
    image.insert(
        "content".to_owned(),
        serde_json::json!({"type": "image", "data": "..."}),
    );
    assert_eq!(chunk_text(&image), "");
}
