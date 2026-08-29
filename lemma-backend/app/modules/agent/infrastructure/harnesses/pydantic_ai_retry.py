"""Re-entering a run whose model stream dropped part-way through.

Providers drop streams. When one does, the answer the user was watching stops
mid-sentence, and the useful thing to do is start the model request again from
the last complete point rather than fail the turn.

Doing that safely needs three things, and each of them is a way to get it wrong:

* **Resume from a snapshot, not from `all_messages()`.** After a drop that list
  has grown a truncated `ModelResponse` and an empty `ModelRequest`; resuming
  from it makes the model continue an answer whose first half was thrown away.
* **Never replay a completed tool call.** The snapshot the node loop takes
  includes the request carrying the tool returns, so pydantic-ai sees the calls
  as answered. Without it, it would re-run them -- which is how a retry charges
  a card twice.
* **Tell the client to discard the partial bubble.** The tokens already streamed
  are on screen; a `stream_reset` event is what removes them before the second
  attempt streams over the top.

A relayed `CancelledError` is only ours to swallow when we are the ones who
cancelled. Otherwise the driver task died under a healthy parent -- the graph
stopped mid-node -- and dropping that would report a broken run as a good one.
"""

from __future__ import annotations

import asyncio
import random
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.infrastructure.harnesses.pydantic_ai_usage import (
    accumulate_usage,
)
from app.modules.agent.infrastructure.transport_errors import (
    is_retryable_stream_error,
    retry_after_seconds,
)

logger = get_logger(__name__)


class HarnessDriverCancelled(Exception):
    """The graph-driving task was cancelled while the run was still healthy.

    An ordinary `Exception` rather than a `CancelledError` subclass on purpose:
    nothing about *this* task is being cancelled, so every `except
    CancelledError` between here and the runner would draw the wrong conclusion
    and re-raise a cancellation that isn't happening. Raised as a failure
    because that is what it is — the graph stopped mid-node and whatever tool
    was executing was cancelled with it.
    """


_RETRY_BACKOFF_CAP_SECONDS = 6.0


def reraise_driver_failure(
    pending_error: BaseException | None,
    *,
    cancelled_by_us: bool,
    agent_run_id: UUID,
) -> None:
    """Decide what a failure relayed out of the driver task means.

    A real error is re-raised so ``run()``'s handlers (ModelHTTPError,
    UsageLimitExceeded, AgentInputRequired, …) still fire.

    A relayed ``CancelledError`` is only ours to drop when we are the ones who
    cancelled — either our own cancellation is already propagating out of the
    consumer loop, or we tore the driver down on the way out. Otherwise the
    driver died under a healthy parent: the graph stopped mid-node, so any tool
    still executing was cancelled with it and its call will never get a result.

    Dropping that silently is how a truncated run came to report success. The
    generator returned normally, so ``run()`` emitted COMPLETED and the run was
    finalized with no error, no log line, and a trailing tool call that nothing
    ever answered — the agent simply stopped, and every layer above said it went
    fine.
    """

    if pending_error is None:
        return
    if not isinstance(pending_error, asyncio.CancelledError):
        raise pending_error
    if cancelled_by_us:
        return
    logger.error(
        "agent.pydantic_ai.driver_cancelled_mid_run.failed",
        agent_run_id=str(agent_run_id),
        exc_info=pending_error,
    )
    raise HarnessDriverCancelled(
        "The agent run was cancelled while a tool call was still running."
    ) from pending_error


async def drive_with_retry(
    drive_once,
    *,
    queue,
    max_attempts: int,
    stream_reset,
    stopped,
    should_stop,
    emit_usage,
) -> None:
    """Run one agent-graph pass, re-entering it after a transient stream drop.

    Lives at module level rather than nested in `_execute` so the retry policy
    can be read — and reasoned about — without the several hundred lines of
    streaming machinery it sits inside.

    `drive_once` publishes the live run and a pre-request history snapshot into
    the `state` dict it is handed. Both are required to resume: the run carries
    the usage already spent, and the snapshot is the history as it stood
    *before* the failing request, which is what keeps a retry from replaying a
    half-written response or re-executing a completed tool.
    """
    carried_usage: dict[str, int] = {}
    resume_history: list[object] | None = None
    attempt = 0
    while True:
        state: dict[str, object] = {}
        try:
            await drive_once(resume_history, state, carried_usage)
            return
        # Deliberately `Exception`, not `BaseException`: a CancelledError must
        # reach the caller untouched so pydantic-graph's scopes unwind in this
        # task. It would have been re-raised here anyway —
        # `is_retryable_stream_error` never retries cancellation — so letting it
        # skip the handler entirely is the same behaviour with less to reason
        # about.
        except Exception as exc:  # noqa: BLE001 — re-raised below when fatal
            run = state.get("run")
            snapshot = state.get("resume_from")
            if (
                run is None
                or snapshot is None
                or attempt + 1 >= max_attempts
                or not is_retryable_stream_error(exc)
            ):
                # Ending for good, so bill before the exception leaves. Every
                # non-retryable exit used to skip this: a provider error, retry
                # exhaustion, and -- the common one -- a pausing tool raising to
                # wait for a person. An approval-gated turn billed zero on every
                # run that asked for one.
                #
                # Here rather than in a `finally` inside the loop because a
                # retryable failure must NOT emit: its tokens are carried into
                # the next attempt's total, and a second event would either
                # double-count for a consumer that sums or make the "exactly one
                # usage total per run" contract false.
                emit_usage()
                raise
            # Resuming re-asks only the request that failed. Completed tool
            # results are replayed from the snapshot rather than re-executed,
            # so no tool ever runs twice.
            accumulate_usage(carried_usage, getattr(run, "usage", None))
            resume_history = list(snapshot)  # type: ignore[arg-type]
            attempt += 1
            logger.warning(
                "agent.pydantic_ai.model_stream_retry.degraded",
                attempt=attempt,
                max_attempts=max_attempts,
                error_type=type(exc).__name__,
                exc_info=True,
            )
            # Tell the client to drop the half-streamed bubble; the replacement
            # response streams from scratch.
            await queue.put(("event", stream_reset()))
            if await should_stop():
                await queue.put(("event", stopped()))
                return
            await asyncio.sleep(retry_after_seconds(exc) or _retry_backoff(attempt))


def _retry_backoff(attempt: int) -> float:
    """Backoff before re-entering the graph, with jitter.

    Short by design: the user is watching a stalled response, and the failure we
    retry is a dropped connection rather than a loaded server. A provider that
    wants longer says so via ``Retry-After``, which takes precedence.
    """
    base = min(_RETRY_BACKOFF_CAP_SECONDS, 0.5 * (3 ** (attempt - 1)))
    return base + random.uniform(0, base / 2)
