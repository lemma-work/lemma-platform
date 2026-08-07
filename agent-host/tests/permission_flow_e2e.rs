//! An ACP agent asks to use a native tool; can Lemma actually answer?
//!
//! The host used to reply `Cancelled` immediately, so every native tool call
//! was denied and no decision could ever be delivered. The fix parks the ACP
//! responder in `PermissionGate` and answers it from a `RESOLVE_PERMISSION`
//! command. Unit tests cover the gate's own bookkeeping; what was missing is
//! proof that the round trip works through the shipped binary — the request
//! reaching Lemma as an event, the decision arriving as a command, and the
//! agent *continuing* afterwards rather than being stuck or cancelled.
//!
//! These are hermetic: the ACP agent is `tests/fixtures/scripted_acp_agent.py`,
//! which refuses to end its turn until its permission requests are answered.
//! `real_harness_e2e.rs` runs the same shape against a real agent asking for a
//! real shell command.
//!
//! Concurrency gets its own case because Claude Code issues parallel tool
//! calls, so two requests can be parked in one session at once and must not
//! answer — or overwrite — each other.

#![cfg(unix)]

use std::sync::Arc;
use std::sync::Mutex;
use std::sync::atomic::{AtomicBool, Ordering};
use std::time::Duration;

use chrono::Utc;
use lemma_agent_host::acp::{AcpCallbacks, AcpDriver, AcpRunRequest, AgentDriver};
use lemma_agent_host::adapters::{AdapterSpec, ResolvedAdapter};
use lemma_agent_host::permissions::{PermissionDecision, PermissionGate};
use lemma_agent_host::protocol::{EventType, JsonMap, RunSpec, RunState};
use serde_json::{Value, json};
use tempfile::TempDir;
use uuid::Uuid;

mod support;

use support::{
    ControlPlane, HostProcess, PermissionAnswer, ShimmedAgents, python, scripted_agent_fixture,
};

/// Run the scripted `permission` agent through the shipped host binary and
/// return what Lemma saw.
async fn run_with_answer(answer: PermissionAnswer) -> (TempDir, ControlPlane, HostProcess) {
    run_scripted("permission", answer).await
}

async fn run_scripted(
    mode: &str,
    answer: PermissionAnswer,
) -> (TempDir, ControlPlane, HostProcess) {
    let directory = TempDir::new().unwrap();
    let shims = ShimmedAgents::install(directory.path(), mode);
    let control = ControlPlane::start(
        &shims.harness_key,
        "Delete the build directory.",
        json!({
            "server_name": "lemma_tools",
            "url": "http://127.0.0.1:1/agent-runtime/conversations/unused/mcp",
            "authorization": "Bearer unused-permission-e2e-token",
        }),
        answer,
    )
    .await;
    let host = HostProcess::start(directory.path(), &control, &shims).await;
    control
        .wait_for(
            "the run to reach a terminal event",
            Duration::from_secs(90),
            ControlPlane::saw_terminal,
        )
        .await;
    (directory, control, host)
}

#[tokio::test]
async fn an_approved_request_lets_the_agent_finish_its_turn() {
    let (_directory, control, host) =
        run_with_answer(PermissionAnswer::Allow("once".to_owned())).await;

    // The request has to reach Lemma before anyone can answer it, and it has to
    // carry the tool-call id the decision will be addressed to.
    let requests = control.permission_requests();
    assert_eq!(
        requests.len(),
        1,
        "exactly one permission request should have reached Lemma; host stderr={}",
        host.stderr()
    );
    assert_eq!(requests[0].object_id.as_deref(), Some("native-shell"));
    assert_eq!(
        requests[0].payload["toolCall"]["title"], "Run rm -rf build",
        "the payload must describe what is being approved, not just that something is"
    );

    // And the decision has to reach the agent as the option the human picked,
    // not merely as "not denied".
    let answer = control.assistant_text();
    assert!(
        answer.contains("LEMMA_PERMISSION_ALLOWED:once"),
        "the agent did not receive the selected option; answer={answer:?}, host stderr={}",
        host.stderr()
    );

    let terminal = control
        .events()
        .into_iter()
        .find(|event| event.event_type == EventType::Terminal)
        .unwrap();
    assert_eq!(
        terminal.payload["state"], "SUCCEEDED",
        "an approved run must end normally; a parked responder that leaked would \
         surface here as a deadline failure"
    );
    host.shutdown().await;
}

#[tokio::test]
async fn a_denied_request_is_delivered_rather_than_left_to_time_out() {
    // A denial that never reaches the host is indistinguishable, from the
    // agent's side, from a human who walked away: it blocks for the full
    // permission timeout. The agent must be told "no" promptly and be free to
    // carry on.
    let (_directory, control, host) = run_with_answer(PermissionAnswer::Deny).await;

    // Denial is also what the pre-fix host did to everything, so "the agent was
    // denied" proves nothing by itself. The request must first have reached
    // Lemma, and the agent must still have been waiting when Lemma answered.
    assert_eq!(
        control.permission_requests().len(),
        1,
        "a denial Lemma was never asked about is the host denying on its own; \
         host stderr={}",
        host.stderr()
    );
    let decisions = control.decisions();
    assert_eq!(decisions.len(), 1, "exactly one decision should be sent");
    assert!(
        !decisions[0].assistant_text.contains("LEMMA_PERMISSION") && !decisions[0].saw_terminal,
        "the agent had already been answered before Lemma decided, so this \
         denial did not come from Lemma: {:?}",
        decisions[0]
    );

    let answer = control.assistant_text();
    assert!(
        answer.contains("LEMMA_PERMISSION_DENIED:cancelled"),
        "a denial must arrive as an ACP `cancelled` outcome; answer={answer:?}, \
         host stderr={}",
        host.stderr()
    );
    let terminal = control
        .events()
        .into_iter()
        .find(|event| event.event_type == EventType::Terminal)
        .unwrap();
    assert_eq!(
        terminal.payload["state"], "SUCCEEDED",
        "denying one tool must not fail the run"
    );
    host.shutdown().await;
}

#[tokio::test]
async fn two_concurrent_requests_stay_separately_parked_and_answerable() {
    // Claude Code runs tool calls in parallel, so two permission requests can
    // be open in one session at once. Each must keep its own gate key and its
    // own `object_id`: sharing either means the older request is denied the
    // moment the newer arrives, and Lemma renders one approval card where
    // there are two.
    //
    // Scope note: this is the healthy shape, where each request names a
    // distinct `toolCall.toolCallId`. It does *not* discriminate the host's
    // fallback for requests that name no tool call — see
    // `colliding_empty_tool_call_ids_...` below for why that path cannot be
    // reached from here.
    let (_directory, control, host) = run_scripted(
        "parallel-permission",
        // Answering the two differently is what proves each was resolved by its
        // own id: one shared key cannot deliver both outcomes.
        PermissionAnswer::AllowThenDeny,
    )
    .await;

    let requests = control.permission_requests();
    assert_eq!(
        requests.len(),
        2,
        "both concurrent requests must reach Lemma; host stderr={}",
        host.stderr()
    );
    let ids = requests
        .iter()
        .filter_map(|event| event.object_id.clone())
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(
        ids.len(),
        2,
        "the two requests must be addressable separately, or Lemma renders one \
         card for both and a decision names the wrong one: {ids:?}"
    );

    // Both were still parked when Lemma answered: neither was auto-denied by
    // the arrival of the other.
    let decisions = control.decisions();
    assert_eq!(decisions.len(), 2, "both requests must have been answered");
    assert_eq!(
        decisions
            .iter()
            .map(|decision| decision.request_id.clone())
            .collect::<std::collections::BTreeSet<_>>(),
        ids,
        "each decision must name the request it answers"
    );

    // Finally, the outcomes actually differed, which is only possible if the
    // two requests were held apart all the way through the gate.
    let answer = control.assistant_text();
    let allowed = answer.contains("LEMMA_PARALLEL_A_ALLOWED:once")
        && answer.contains("LEMMA_PARALLEL_B_DENIED:cancelled");
    let reversed = answer.contains("LEMMA_PARALLEL_B_ALLOWED:once")
        && answer.contains("LEMMA_PARALLEL_A_DENIED:cancelled");
    assert!(
        allowed || reversed,
        "exactly one request should have been approved and the other denied; \
         answer={answer:?}, decisions={decisions:?}, host stderr={}",
        host.stderr()
    );
    host.shutdown().await;
}

#[tokio::test]
async fn colliding_empty_tool_call_ids_strand_one_request_and_merge_both_cards() {
    // A *missing* toolCallId cannot be reached from a real ACP agent:
    // `ToolCallUpdate.tool_call_id` is required by
    // agent-client-protocol-schema v1, so the SDK rejects such a request with
    // `Invalid params` before the host's handler runs. A *blank* one is the
    // reachable variant, and `tool_call_id()` now treats it as absent so each
    // request still gets its own gate key and its own approval card.
    //
    // What an adapter *can* send is a blank id, and that is not covered:
    // `tool_call_id()` finds the key and returns `Some("")`, so the fallback
    // never fires and both requests land on the key `(run, "")`. This test
    // states the behaviour we want; today it fails, in two ways that are worth
    // seeing separately.
    let directory = TempDir::new().unwrap();
    let shims = ShimmedAgents::install(directory.path(), "parallel-permission-empty-id");
    let control = ControlPlane::start(
        &shims.harness_key,
        "Delete the build directory.",
        json!({
            "server_name": "lemma_tools",
            "url": "http://127.0.0.1:1/agent-runtime/conversations/unused/mcp",
            "authorization": "Bearer unused-permission-e2e-token",
        }),
        PermissionAnswer::AllowThenDeny,
    )
    .await;
    let host = HostProcess::start(directory.path(), &control, &shims).await;
    control
        .wait_for(
            "both permission requests to reach Lemma",
            Duration::from_secs(60),
            |control| control.permission_requests().len() >= 2,
        )
        .await;

    // 1. Lemma cannot tell the two apart, so its UI overwrites one approval
    //    card with the other and a decision names an ambiguous request.
    let ids = control
        .permission_requests()
        .iter()
        .filter_map(|event| event.object_id.clone())
        .collect::<std::collections::BTreeSet<_>>();
    assert_eq!(
        ids.len(),
        2,
        "both requests were reported under the same object_id: {ids:?}"
    );

    // 2. Only one of them can ever be answered, so the other waits out the
    //    thirty-minute permission timeout and the run never finishes.
    control
        .wait_for(
            "the run to finish once both requests are answered",
            Duration::from_secs(60),
            ControlPlane::saw_terminal,
        )
        .await;
    host.shutdown().await;
}

// ---------------------------------------------------------------------------
// The timeout path.
// ---------------------------------------------------------------------------

#[derive(Default)]
struct Capture {
    events: Mutex<Vec<(EventType, JsonMap)>>,
}

impl AcpCallbacks for Capture {
    fn before_prompt(&self, _provider_session_id: &str) -> anyhow::Result<()> {
        Ok(())
    }

    fn event(
        &self,
        event_type: EventType,
        _object_id: Option<String>,
        payload: JsonMap,
    ) -> anyhow::Result<()> {
        self.events.lock().unwrap().push((event_type, payload));
        Ok(())
    }
}

fn scripted_adapter(log: &std::path::Path) -> ResolvedAdapter {
    ResolvedAdapter {
        spec: AdapterSpec {
            key: "scripted".into(),
            display_name: "Scripted".into(),
            adapter_version: "1.0.0".into(),
            command: "python3".into(),
            args: vec![
                scripted_agent_fixture().to_string_lossy().into_owned(),
                log.to_string_lossy().into_owned(),
                "permission".to_owned(),
            ],
            upstream_command: "python3".into(),
            upstream_version_args: vec!["--version".into()],
            minimum_upstream_version: None,
            distribution: "native".into(),
            artifact_integrity: None,
            license: "test".into(),
        },
        command: python(),
        upstream_command: python(),
        upstream_version: Some("test".into()),
    }
}

#[tokio::test]
async fn an_unanswered_request_is_denied_when_the_timeout_elapses() {
    // The production timeout is thirty minutes, which is right for a human but
    // impossible to wait out in a suite, so this drives the driver directly
    // with a short one. What matters is the shape: nobody answers, the agent is
    // told `cancelled` rather than left hanging, and the run still completes —
    // a forgotten prompt must not pin an adapter open until the run deadline.
    let scratch = TempDir::new().unwrap();
    let log = scratch.path().join("acp.jsonl");
    let adapter = scripted_adapter(&log);
    let callbacks = Arc::new(Capture::default());
    let started = std::time::Instant::now();

    let outcome = AcpDriver
        .run(
            AcpRunRequest {
                adapter,
                run_spec: RunSpec {
                    agent_run_id: Uuid::new_v4(),
                    conversation_id: Uuid::new_v4(),
                    harness_id: Uuid::new_v4(),
                    profile_revision: "timeout-e2e".into(),
                    model_name: None,
                    config_selections: JsonMap::new(),
                    system_prompt: String::new(),
                    prompt: vec![json!({"type": "text", "text": "Delete the build directory."})],
                    resume_session_id: None,
                    context: JsonMap::new(),
                    mcp: Value::Null,
                    run_deadline: Utc::now() + chrono::Duration::minutes(2),
                },
                scratch_directory: scratch.path().join("run"),
                mcp_server: None,
                can_load_session: false,
                permissions: PermissionGate::new(),
                permission_timeout: Duration::from_millis(300),
                cancel: lemma_agent_host::acp::never_cancelled(),
                cancel_grace: Duration::from_secs(5),
            },
            callbacks.clone(),
        )
        .await
        .unwrap();

    assert_eq!(outcome.state, RunState::Succeeded);
    assert!(
        started.elapsed() < Duration::from_secs(30),
        "the run must not wait out the run deadline for one unanswered prompt"
    );
    let events = callbacks.events.lock().unwrap();
    assert!(
        events
            .iter()
            .any(|(kind, _)| *kind == EventType::PermissionRequest),
        "the request should still have been reported to Lemma before it expired"
    );
    let answer = events
        .iter()
        .filter(|(kind, _)| *kind == EventType::AgentMessageChunk)
        .filter_map(|(_, payload)| payload.get("text").and_then(Value::as_str))
        .collect::<String>();
    assert!(
        answer.contains("LEMMA_PERMISSION_DENIED:cancelled"),
        "an expired request must reach the agent as a denial; answer={answer:?}"
    );
}

#[tokio::test]
async fn a_decision_cannot_resolve_another_runs_request() {
    // The gate is keyed by (run, request). If it were keyed by request id alone
    // then two concurrent runs whose adapters both label a tool call `call-1`
    // would answer each other's prompts.
    let gate = PermissionGate::new();
    let mine = Uuid::now_v7();
    let theirs = Uuid::now_v7();
    let waiting = Arc::new(AtomicBool::new(false));
    let waiter = {
        let gate = gate.clone();
        let waiting = Arc::clone(&waiting);
        tokio::spawn(async move {
            waiting.store(true, Ordering::SeqCst);
            gate.wait(mine, "call-1".to_owned(), Duration::from_secs(5))
                .await
        })
    };
    while !waiting.load(Ordering::SeqCst) {
        tokio::task::yield_now().await;
    }
    let allow = PermissionDecision::Allow {
        option_id: "once".to_owned(),
    };
    for _ in 0..50 {
        assert!(
            !gate.resolve(theirs, "call-1", allow.clone()),
            "another run's decision must not be accepted"
        );
        if gate.resolve(mine, "call-1", allow.clone()) {
            break;
        }
        tokio::time::sleep(Duration::from_millis(10)).await;
    }
    assert_eq!(waiter.await.unwrap(), allow);
}
