//! Saying why a run ended, in words a person can act on.

use super::{EventType, Journal, JsonMap, RunState, Uuid, Value};

/// End a run and say why, on both paths that carry a reason upstream.
///
/// The message goes in the terminal *event* and in the terminal *checkpoint*,
/// because the two have different lifetimes. An event is pruned from the outbox
/// once Lemma acknowledges it, so a run that failed an hour ago has only its
/// checkpoint left — and that used to be written empty, which is why a dead run
/// could be inspected afterwards and offer nothing but `FAILED` and `{}`. The
/// checkpoint is also the path that survives Lemma never acknowledging the
/// event at all.
pub(crate) fn terminal_failure(
    journal: &Journal,
    target_id: Uuid,
    run_id: Uuid,
    lease_epoch: u32,
    state: RunState,
    message: &str,
) -> anyhow::Result<()> {
    terminal_failure_detail(
        journal,
        target_id,
        run_id,
        lease_epoch,
        state,
        message,
        false,
    )
}

/// The shared body, plus the one thing a caller may say about its message.
///
/// `supersedes_stream` means "this message is a rewrite of text the agent has
/// already streamed". An adapter reporting its own failure does it twice: once
/// as an ordinary `agent_message_chunk`, which Lemma turns into assistant text
/// in the transcript, and again when the turn ends. So a signed-out Claude Code
/// produced a bare "Failed to authenticate: OAuth session expired…" message
/// *and* the card saying the same thing in words the user can act on.
///
/// That is worse than untidy. Lemma only offers Retry on a failed run whose
/// messages are all the user's (`AgentRun.is_safely_retryable`), because
/// retrying a run that produced output can duplicate work -- so the stray
/// assistant message is also what removed the button. The one failure a retry
/// obviously fixes was the one failure that never offered one.
///
/// Set it only where the message really is a rewrite. A run that answered for
/// three paragraphs and then hit its deadline keeps all three, and keeps
/// blocking Retry, which is correct.
#[allow(clippy::fn_params_excessive_bools)]
pub(crate) fn terminal_failure_detail(
    journal: &Journal,
    target_id: Uuid,
    run_id: Uuid,
    lease_epoch: u32,
    state: RunState,
    message: &str,
    supersedes_stream: bool,
) -> anyhow::Result<()> {
    let mut detail = JsonMap::new();
    detail.insert("state".to_owned(), serde_json::to_value(state)?);
    detail.insert("message".to_owned(), Value::String(message.to_owned()));
    if supersedes_stream {
        detail.insert("supersedes_stream".to_owned(), Value::Bool(true));
    }
    journal.append_event(
        target_id,
        run_id,
        lease_epoch,
        EventType::Terminal,
        None,
        detail.clone(),
    )?;
    journal.checkpoint(target_id, run_id, lease_epoch, state, &detail)?;
    Ok(())
}
