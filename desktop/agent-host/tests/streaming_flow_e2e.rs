//! Real host process, ACP subprocess, journal, and HTTP delivery. The agent
//! cannot finish until the receiver observes its first text and releases it.

#![cfg(unix)]

use std::time::Duration;

use lemma_agent_host::protocol::EventType;
use serde_json::json;
use tempfile::TempDir;

mod support;

use support::{ControlPlane, HostProcess, PermissionAnswer, ShimmedAgents};

async fn streaming_run(
    mode: &str,
    lose_ack: bool,
) -> (TempDir, ShimmedAgents, ControlPlane, HostProcess) {
    let directory = TempDir::new().unwrap();
    let shims = ShimmedAgents::install(directory.path(), mode);
    let control = ControlPlane::start(
        &shims.harness_key,
        "Stream a reply.",
        json!({"server_name": "lemma_tools"}),
        PermissionAnswer::Deny,
    )
    .await;
    if lose_ack {
        control.lose_the_first_append_ack();
    }
    if mode == "stream-deadline" {
        control.set_run_budget(chrono::Duration::seconds(5));
    }
    let host = HostProcess::start(directory.path(), &control, &shims).await;
    (directory, shims, control, host)
}

#[tokio::test]
async fn live_unicode_text_survives_a_lost_ack_without_repeating_the_prompt() {
    let (_directory, shims, control, host) = streaming_run("stream", true).await;
    control
        .wait_for(
            "text while the agent is still running",
            Duration::from_secs(90),
            |control| control.assistant_text().contains("前 café 👩🏽‍💻\n") || control.saw_terminal(),
        )
        .await;
    assert!(
        !control.saw_terminal(),
        "text must be delivered before the agent is released: {}",
        host.stderr()
    );
    assert_eq!(control.assistant_text(), "前 café 👩🏽‍💻\n");
    std::fs::write(shims.acp_log.with_extension("release"), "continue").unwrap();
    control
        .wait_for(
            "complete stream",
            Duration::from_secs(20),
            ControlPlane::saw_terminal,
        )
        .await;
    host.shutdown().await;

    let events = control.events();
    assert_eq!(control.assistant_text(), "前 café 👩🏽‍💻\nsecond line\n完成");
    let terminals = events
        .iter()
        .filter(|event| event.event_type == EventType::Terminal)
        .collect::<Vec<_>>();
    assert_eq!(terminals.len(), 1);
    assert_eq!(terminals[0].payload["state"], "SUCCEEDED");
    assert!(
        events
            .windows(2)
            .all(|pair| pair[1].sequence == pair[0].sequence + 1)
    );
    assert!(
        events
            .iter()
            .any(|event| event.event_type == EventType::AgentThoughtChunk)
    );
    let durable_text = events
        .iter()
        .filter(|event| event.event_type == EventType::AgentMessageUpsert)
        .filter_map(|event| {
            event
                .payload
                .get("text")
                .and_then(serde_json::Value::as_str)
        })
        .collect::<String>();
    assert_eq!(
        durable_text,
        control.assistant_text(),
        "reloading the conversation must retain the full live answer"
    );
    let attempts = control.append_attempts();
    let first_sequence = attempts[0][0];
    assert!(
        attempts
            .iter()
            .filter(|batch| batch.contains(&first_sequence))
            .count()
            >= 2,
        "the lost acknowledgement must force a replay"
    );
    assert_eq!(
        shims
            .traffic()
            .iter()
            .filter(|entry| entry["message"]["method"] == "session/prompt")
            .count(),
        1,
        "transport recovery must not repeat provider work"
    );
}

#[tokio::test]
async fn a_crashed_agent_keeps_partial_text_and_reports_one_failed_terminal() {
    let (_directory, _shims, control, host) = streaming_run("stream-crash", false).await;
    control
        .wait_for(
            "failed terminal after agent exit",
            Duration::from_secs(90),
            ControlPlane::saw_terminal,
        )
        .await;
    host.shutdown().await;
    assert_eq!(control.assistant_text(), "前 café 👩🏽‍💻\n");
    let events = control.events();
    let terminals = events
        .iter()
        .filter(|event| event.event_type == EventType::Terminal)
        .collect::<Vec<_>>();
    assert_eq!(terminals.len(), 1);
    assert_eq!(terminals[0].payload["state"], "FAILED");
    assert!(
        events
            .iter()
            .any(|event| event.event_type == EventType::AgentMessageUpsert),
        "partial text must have a durable replay snapshot"
    );
}

#[tokio::test]
async fn a_deadline_seals_partial_text_before_failing_the_run() {
    let (_directory, _shims, control, host) = streaming_run("stream-deadline", false).await;
    control
        .wait_for(
            "the deadline to stop a stalled provider",
            Duration::from_secs(90),
            ControlPlane::saw_terminal,
        )
        .await;
    host.shutdown().await;
    assert_eq!(control.assistant_text(), "前 café 👩🏽‍💻\n");
    let events = control.events();
    let terminal = events
        .iter()
        .find(|event| event.event_type == EventType::Terminal)
        .unwrap();
    assert_eq!(terminal.payload["state"], "FAILED");
    assert!(
        terminal.payload["message"]
            .as_str()
            .unwrap()
            .contains("deadline")
    );
    let saved = events
        .iter()
        .find(|event| event.event_type == EventType::AgentMessageUpsert)
        .unwrap();
    assert_eq!(saved.payload["text"], control.assistant_text());
    assert!(saved.sequence < terminal.sequence);
}

#[tokio::test]
async fn a_host_restart_preserves_partial_text_without_dispatching_the_prompt_again() {
    let (directory, shims, control, host) = streaming_run("stream", false).await;
    control
        .wait_for(
            "live text before restarting the host",
            Duration::from_secs(90),
            |control| !control.assistant_text().is_empty() || control.saw_terminal(),
        )
        .await;
    assert!(!control.saw_terminal());
    host.shutdown().await;
    let restarted = HostProcess::resume(directory.path(), &control, &shims);
    control
        .wait_for(
            "interrupted-run recovery",
            Duration::from_secs(30),
            ControlPlane::saw_terminal,
        )
        .await;
    restarted.shutdown().await;
    let events = control.events();
    let terminal = events
        .iter()
        .find(|event| event.event_type == EventType::Terminal)
        .unwrap();
    assert_eq!(terminal.payload["state"], "DISPATCH_UNKNOWN");
    let saved = events
        .iter()
        .find(|event| event.event_type == EventType::AgentMessageUpsert)
        .expect("recovery must seal the acknowledged partial text for durable replay");
    assert_eq!(saved.payload["text"], control.assistant_text());
    assert!(saved.sequence < terminal.sequence);
    assert_eq!(
        shims
            .traffic()
            .iter()
            .filter(|entry| entry["message"]["method"] == "session/prompt")
            .count(),
        1
    );
}
