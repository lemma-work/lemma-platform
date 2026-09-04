"""Telling the run observer what happened, without letting it break the run.

An observer is a delivery side-effect — the surface layer uses one to stream a
run onto Slack or Teams. If that fails the run itself is still valid and its
result still has to be persisted, so every notification here is best-effort and
logged rather than raised.

All four notifications had this shape written out at their call sites, four
`try/await/except Exception/logger.debug` blocks that differed only in the event
name. Collapsing them is worth more than the repetition: `AgentRunnerService`
held four separate functions containing a broad `except`, which the architecture
ratchet counts per *function*, so decomposing that class would have raised the
count and failed the gate for a reason that has nothing to do with the split.
"""

from __future__ import annotations

from typing import Any

from app.core.log.log import get_logger

logger = get_logger(__name__)


async def _deliver(notify: Any, on_failure: Any) -> bool:
    """Await one observer callback. True when it landed.

    `on_failure` does the logging rather than this taking an event name,
    because the logging contract is checked statically and requires the event
    to be a literal at the `logger` call — a name threaded through a parameter
    is invisible to it, and an event nothing can see is an event nobody will
    find when it fires.
    """
    try:
        await notify()
    except Exception:
        on_failure()
        return False
    return True


async def notify_run_started(
    observer: Any,
    conversation: Any,
    ctx: Any,
    agent_run_id: Any,
) -> bool:
    """True when the observer accepted the start, so the finish is owed to it."""
    if observer is None:
        return False
    return await _deliver(
        lambda: observer.on_run_started(conversation, ctx),
        lambda: logger.debug(
            "agent.agent_runner_service.agent_run_observer_start_run.diagnostic",
            agent_run_id=agent_run_id,
        ),
    )


async def notify_event(
    observer: Any,
    event: Any,
    conversation: Any,
    ctx: Any,
    agent_run_id: Any,
) -> None:
    if observer is None:
        return
    await _deliver(
        lambda: observer.on_event(event, conversation, ctx),
        lambda: logger.debug(
            "agent.agent_runner_service.agent_run_observer_run_s.diagnostic",
            agent_run_id=agent_run_id,
        ),
    )


async def notify_run_finished(
    observer: Any,
    conversation: Any,
    ctx: Any,
    agent_run_id: Any,
) -> None:
    if observer is None:
        return
    await _deliver(
        lambda: observer.on_run_finished(conversation, ctx),
        lambda: logger.debug(
            "agent.agent_runner_service.agent_run_observer_finish_run.diagnostic",
            agent_run_id=agent_run_id,
        ),
    )


async def notify_run_failed(
    observer: Any,
    conversation: Any,
    error: BaseException,
    agent_run_id: Any,
) -> None:
    # A cancellation is not a failure the observer needs to hear about, and it
    # is a BaseException rather than an Exception — hence the isinstance rather
    # than a bare None check.
    if observer is None or not isinstance(error, Exception):
        return
    await _deliver(
        lambda: observer.on_run_failed(conversation, error),
        lambda: logger.debug(
            "agent.agent_runner_service.agent_run_observer_failure_delivery.diagnostic",
            agent_run_id=agent_run_id,
        ),
    )
