//! Stopping a run has to be a request before it is a killing.
//!
//! The host used to answer `CANCEL_RUN` by aborting the run task and killing
//! the adapter's process tree. That ends the turn, but it ends it at the worst
//! possible moment: the provider has not yet written the session file that the
//! conversation's *next* turn loads, so cancelling one message could silently
//! cost the conversation its whole history. ACP has `session/cancel` for
//! exactly this — the agent stops its own turn and reports `cancelled`.
//!
//! These run the shipped binary against `tests/fixtures/scripted_acp_agent.py`
//! in `cancel` mode, which works until it is told to stop and then acknowledges
//! over the wire. A host that skips straight to the kill never produces that
//! acknowledgement, which is what separates the two paths here.

#![cfg(unix)]

use std::time::Duration;

use lemma_agent_host::protocol::{EventType, RunState};
use serde_json::json;
use tempfile::TempDir;

mod support;

use support::{ControlPlane, HostProcess, PermissionAnswer, ShimmedAgents};

/// Start a run, let the agent get going, then cancel it.
async fn run_cancelled() -> (TempDir, ControlPlane, HostProcess) {
    let directory = TempDir::new().unwrap();
    let shims = ShimmedAgents::install(directory.path(), "cancel");
    let control = ControlPlane::start(
        &shims.harness_key,
        "Work until you are told to stop.",
        json!({
            "server_name": "lemma_tools",
            "url": "http://127.0.0.1:1/agent-runtime/conversations/unused/mcp",
            "authorization": "Bearer unused-cancel-e2e-token",
        }),
        PermissionAnswer::Ignore,
    )
    .await;
    // Cancel only once the agent is genuinely mid-turn, so this exercises
    // stopping work rather than racing the run's start.
    control.cancel_when_text_contains("LEMMA_CANCEL_WORKING");
    let host = HostProcess::start(directory.path(), &control, &shims).await;
    control
        .wait_for(
            "the cancelled run to reach a terminal event",
            Duration::from_secs(90),
            ControlPlane::saw_terminal,
        )
        .await;
    (directory, control, host)
}

#[tokio::test]
async fn a_cancelled_run_is_asked_to_stop_over_acp() {
    let (_directory, control, host) = run_cancelled().await;

    let text = control.assistant_text();
    assert!(
        text.contains("LEMMA_CANCEL_WORKING"),
        "the agent should have started working, got {text:?}"
    );
    assert!(
        text.contains("LEMMA_CANCEL_ACKED"),
        "the agent never saw session/cancel, so the host killed it instead: {text:?}"
    );

    host.shutdown().await;
}

#[tokio::test]
async fn the_agents_own_cancelled_stop_reason_is_what_lemma_records() {
    let (_directory, control, host) = run_cancelled().await;

    let terminal = control
        .events()
        .into_iter()
        .find(|event| event.event_type == EventType::Terminal)
        .expect("a cancelled run still reports a terminal event");

    assert_eq!(
        terminal
            .payload
            .get("state")
            .and_then(|state| state.as_str()),
        Some(
            serde_json::to_value(RunState::Cancelled)
                .unwrap()
                .as_str()
                .unwrap()
        ),
    );
    assert_eq!(
        terminal
            .payload
            .get("stop_reason")
            .and_then(|reason| reason.as_str()),
        Some("cancelled"),
        "the stop reason must be the agent's own, not one the host invented"
    );

    host.shutdown().await;
}
