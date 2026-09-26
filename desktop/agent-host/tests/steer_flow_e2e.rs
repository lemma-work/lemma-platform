//! A message sent while a turn is running reaches that turn, when it can.
//!
//! ACP v1 gives a running `session/prompt` no way to take more input. The
//! Claude Code and Codex adapters Lemma pins both implement `_session/steering`
//! for it, and advertise it in `initialize`; an adapter that does not is left
//! alone and the message waits for Lemma's follow-up turn.
//!
//! These run the shipped binary against `tests/fixtures/scripted_acp_agent.py`
//! replaying `scenarios/steer*.json`. The steering scenario only finishes its
//! turn once a steer has arrived and been answered, so a host that never sent
//! one cannot pass by finishing early.

#![cfg(unix)]

use std::time::Duration;

use lemma_agent_host::protocol::{EventType, SteerResultPayload};
use serde_json::{Value, json};
use tempfile::TempDir;

mod support;

use support::{ControlPlane, HostProcess, PermissionAnswer, ShimmedAgents};

const MESSAGE_ID: &str = "7a0c3f4e-5d1b-4f7e-9d6a-2b8c1e0f9a11";

async fn control_plane(shims: &ShimmedAgents) -> ControlPlane {
    ControlPlane::start(
        &shims.harness_key,
        "Start the first task.",
        json!({
            "server_name": "lemma_tools",
            "token": "unused-steer-e2e-token",
        }),
        PermissionAnswer::Ignore,
    )
    .await
}

fn result_of(control: &ControlPlane) -> Option<(Option<String>, SteerResultPayload)> {
    control.steer_results().into_iter().next().map(|event| {
        let payload: SteerResultPayload =
            serde_json::from_value(Value::Object(event.payload.into_iter().collect())).unwrap();
        (event.object_id, payload)
    })
}

fn published_steering(control: &ControlPlane) -> Option<bool> {
    control
        .published_snapshots()
        .iter()
        .rev()
        .find_map(|snapshot| snapshot.pointer("/capabilities/steering")?.as_bool())
}

#[tokio::test]
async fn a_message_sent_mid_turn_is_injected_into_that_turn() {
    let directory = TempDir::new().unwrap();
    let shims = ShimmedAgents::install(directory.path(), "steer");
    let control = control_plane(&shims).await;
    control.steer_when_text_contains(
        "Working on the first request",
        MESSAGE_ID,
        "Also update the changelog.",
    );
    let host = HostProcess::start(directory.path(), &control, &shims).await;
    control
        .wait_for(
            "the steered run to finish",
            Duration::from_secs(90),
            ControlPlane::saw_terminal,
        )
        .await;

    assert_eq!(
        published_steering(&control),
        Some(true),
        "an adapter advertising _session/steering is published as able to steer"
    );
    let (object_id, payload) = result_of(&control).expect("the host reported the steer");
    assert_eq!(object_id.as_deref(), Some(MESSAGE_ID));
    assert_eq!(
        payload,
        SteerResultPayload {
            delivered: true,
            detail: None
        }
    );
    let text = control.assistant_text();
    assert!(
        text.contains("Also noted: Also update the changelog."),
        "the agent never saw the steered message: {text:?}"
    );
    // Delivered inside the one turn: a steer is not a second prompt.
    let prompts = shims
        .traffic()
        .into_iter()
        .filter(|record| record.pointer("/message/method") == Some(&json!("session/prompt")))
        .count();
    assert_eq!(prompts, 1);
    // The result is ordered in the run's stream where the steer landed:
    // after what the agent said before it, before what it said after.
    let events = control.events();
    let steered_at = events
        .iter()
        .position(|event| event.event_type == EventType::SteerResult)
        .unwrap();
    let terminal_at = events
        .iter()
        .position(|event| event.event_type == EventType::Terminal)
        .unwrap();
    assert!(steered_at < terminal_at);

    host.shutdown().await;
}

#[tokio::test]
async fn an_agent_without_steering_is_never_sent_the_extension() {
    let directory = TempDir::new().unwrap();
    let shims = ShimmedAgents::install(directory.path(), "steer-unsupported");
    let control = control_plane(&shims).await;
    control.steer_when_text_contains("Working without steering", MESSAGE_ID, "Too late?");
    let host = HostProcess::start(directory.path(), &control, &shims).await;
    control
        .wait_for(
            "the host to report the steer it could not deliver",
            Duration::from_secs(90),
            |control| result_of(control).is_some(),
        )
        .await;
    // Only now let the turn finish, so the steer provably arrived mid-turn.
    std::fs::write(shims.acp_log.with_extension("release"), "continue").unwrap();
    control
        .wait_for(
            "the run to finish",
            Duration::from_secs(90),
            ControlPlane::saw_terminal,
        )
        .await;

    assert_eq!(published_steering(&control), Some(false));
    let (object_id, payload) = result_of(&control).unwrap();
    assert_eq!(object_id.as_deref(), Some(MESSAGE_ID));
    assert_eq!(
        payload,
        SteerResultPayload {
            delivered: false,
            detail: Some(lemma_agent_host::acp::STEER_UNSUPPORTED.to_owned()),
        }
    );
    assert!(
        !shims.traffic().iter().any(|record| {
            record.pointer("/message/method") == Some(&json!("_session/steering"))
        }),
        "an agent that did not advertise steering was sent the extension anyway"
    );

    host.shutdown().await;
}

/// An adapter that answers a steer by starting a turn of its own.
///
/// Both pinned adapters do this when the turn they were asked to steer has
/// already ended. That turn belongs to no Lemma run, so the host stops it and
/// says the message was not delivered -- Lemma's follow-up turn carries it.
#[tokio::test]
async fn a_steer_that_started_its_own_turn_is_cancelled_and_not_counted() {
    let directory = TempDir::new().unwrap();
    let shims = ShimmedAgents::install(directory.path(), "steer-too-late");
    let control = control_plane(&shims).await;
    control.steer_when_text_contains("Finishing the only request", MESSAGE_ID, "Late news.");
    let host = HostProcess::start(directory.path(), &control, &shims).await;
    control
        .wait_for(
            "the run to finish",
            Duration::from_secs(90),
            ControlPlane::saw_terminal,
        )
        .await;

    let (object_id, payload) = result_of(&control).unwrap();
    assert_eq!(object_id.as_deref(), Some(MESSAGE_ID));
    assert_eq!(
        payload,
        SteerResultPayload {
            delivered: false,
            detail: Some(lemma_agent_host::acp::STEER_TURN_ENDED.to_owned()),
        }
    );
    // The scenario only finishes once it has been told to stop, so reaching a
    // terminal event at all is the proof; this names it.
    assert!(
        shims
            .traffic()
            .iter()
            .any(|record| { record.pointer("/message/method") == Some(&json!("session/cancel")) })
    );

    host.shutdown().await;
}
