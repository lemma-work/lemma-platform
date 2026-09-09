//! How a run ended, and whether it was asked to.

use super::{RunSpec, RunState, watch};

/// How a finished turn is recorded, given what we asked of it.
///
/// What *we* did outranks what the agent says about it. ACP requires the
/// `cancelled` stop reason for a turn stopped by `session/cancel` — and
/// `OpenCode` reports `end_turn`, verified against the real agent in
/// `real_harness_e2e`. Taking that literally records a run the user cancelled
/// as having *succeeded*, presenting a truncated answer as the whole one. We
/// know whether we asked it to stop, so that is what the run is.
pub(crate) fn run_outcome(asked_to_stop: bool, stop_reason: &str) -> (RunState, Option<String>) {
    if asked_to_stop {
        if stop_reason != "cancelled" {
            tracing::debug!(
                stop_reason,
                "the agent ended a cancelled turn without ACP's cancelled stop reason"
            );
        }
        return (RunState::Cancelled, None);
    }
    outcome_for(stop_reason)
}

/// How one ACP stop reason ends a Lemma run, and what to say about it.
///
/// ACP names five ways a turn can end. Only `end_turn` and `cancelled` speak
/// for themselves; the rest are distinct, actionable conditions that all used
/// to arrive as an undifferentiated `FAILED`. They stay `FAILED` — the turn
/// genuinely did not finish the work — but they now say which ceiling was hit,
/// because the difference between "out of context" and "the agent crashed" is
/// the difference between retrying usefully and retrying forever.
pub(crate) fn outcome_for(stop_reason: &str) -> (RunState, Option<String>) {
    match stop_reason {
        "end_turn" => (RunState::Succeeded, None),
        "cancelled" => (RunState::Cancelled, None),
        "max_tokens" => (
            RunState::Failed,
            Some(
                "The agent stopped because it reached its maximum context \
                 length. Anything it had already written is above; continue in \
                 a new conversation to give it room."
                    .to_owned(),
            ),
        ),
        "max_turn_requests" => (
            RunState::Failed,
            Some(
                "The agent stopped because it reached its limit on tool calls \
                 for a single turn. Ask it to continue, or narrow the task."
                    .to_owned(),
            ),
        ),
        "refusal" => (
            RunState::Failed,
            Some(
                "The agent declined to continue with this request. It will not \
                 see this prompt again, so rephrasing it is worth trying."
                    .to_owned(),
            ),
        ),
        other => (
            RunState::Failed,
            Some(format!("The agent ended its turn unexpectedly ({other}).")),
        ),
    }
}

/// A cancel signal nothing will ever raise.
///
/// For callers with no control plane behind them — a local smoke run, a test
/// exercising some other part of the driver — so they do not have to invent a
/// channel they will never send on.
#[must_use]
pub fn never_cancelled() -> watch::Receiver<bool> {
    watch::channel(false).1
}

/// Resolves once Lemma has asked for this run to stop.
///
/// Never resolves if the sender is dropped: the run task owns that sender for
/// its whole life, so a dropped one means the supervisor is already tearing
/// this run down by other means and a spurious `session/cancel` would only
/// race it.
pub(crate) async fn cancel_requested(cancel: &mut watch::Receiver<bool>) {
    if *cancel.borrow() {
        return;
    }
    while cancel.changed().await.is_ok() {
        if *cancel.borrow() {
            return;
        }
    }
    std::future::pending::<()>().await;
}

/// The session this run should continue, or `None` to open a new one.
///
/// Lemma only sends a `resume_session_id` for a harness that advertised
/// `loadSession`, but the host checks its own probe too rather than trusting a
/// stale server-side capability record: a `session/load` an agent does not
/// implement costs a round trip on every turn before falling back.
pub(crate) fn session_to_resume(run_spec: &RunSpec, can_load_session: bool) -> Option<String> {
    if !can_load_session {
        return None;
    }
    run_spec
        .resume_session_id
        .as_deref()
        .map(str::trim)
        .filter(|id| !id.is_empty())
        .map(str::to_owned)
}
