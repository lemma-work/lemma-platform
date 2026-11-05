"""Telling the run observer what happened, without letting it break the run.

An observer is a delivery side-effect — the surface layer uses one to stream a
run onto Slack or Teams. If that fails the run itself is still valid and its
result still has to be persisted, so every notification here is best-effort and
logged rather than raised.

Logged at warning with the traceback, not at debug: what fails here is the
person's answer not reaching the platform they asked on, and at debug that
happened with no trace at all in production.

All four notifications had this shape written out at their call sites, four
`try/await/except Exception/logger.debug` blocks that differed only in the event
name. Collapsing them is worth more than the repetition: `AgentRunnerService`
held four separate functions containing a broad `except`, which the architecture
ratchet counts per *function*, so decomposing that class would have raised the
count and failed the gate for a reason that has nothing to do with the split.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.contracts.run_observer import RunObserver
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import AgentEvent
from app.modules.agent.domain.context import ToolContext

logger = get_logger(__name__)


async def _deliver(
    notify: Callable[[], Awaitable[None]], on_failure: Callable[[Exception], None]
) -> bool:
    """Await one observer callback. True when it landed.

    `on_failure` does the logging rather than this taking an event name,
    because the logging contract is checked statically and requires the event
    to be a literal at the `logger` call — a name threaded through a parameter
    is invisible to it, and an event nothing can see is an event nobody will
    find when it fires. It is handed the exception so the traceback is logged.
    """
    try:
        await notify()
    except Exception as exc:
        on_failure(exc)
        return False
    return True


async def notify_run_started(
    observer: RunObserver | None,
    conversation: Conversation,
    ctx: ToolContext,
    agent_run_id: UUID,
) -> bool:
    """True when the observer accepted the start.

    The finish is owed to the observer either way: a start that failed (a typing
    bubble, a stream that would not open) leaves the run's answer still to be
    delivered, and it used to be skipped along with the start.
    """
    if observer is None:
        return False
    return await _deliver(
        lambda: observer.on_run_started(conversation, ctx),
        lambda exc: logger.warning(
            "agent.run_observer.start_delivery.degraded",
            agent_run_id=agent_run_id,
            exc_info=exc,
        ),
    )


async def notify_event(
    observer: RunObserver | None,
    event: AgentEvent,
    conversation: Conversation,
    ctx: ToolContext,
    agent_run_id: UUID,
) -> None:
    if observer is None:
        return
    await _deliver(
        lambda: observer.on_event(event, conversation, ctx),
        lambda exc: logger.warning(
            "agent.run_observer.event_delivery.degraded",
            agent_run_id=agent_run_id,
            exc_info=exc,
        ),
    )


async def notify_run_finished(
    observer: RunObserver | None,
    conversation: Conversation,
    ctx: ToolContext,
    agent_run_id: UUID,
) -> None:
    if observer is None:
        return
    await _deliver(
        lambda: observer.on_run_finished(conversation, ctx),
        lambda exc: logger.warning(
            "agent.run_observer.finish_delivery.degraded",
            agent_run_id=agent_run_id,
            exc_info=exc,
        ),
    )


async def notify_run_failed(
    observer: RunObserver | None,
    conversation: Conversation,
    error: BaseException,
    agent_run_id: UUID,
) -> None:
    # A cancellation is not a failure the observer needs to hear about, and it
    # is a BaseException rather than an Exception — hence the isinstance rather
    # than a bare None check.
    if observer is None or not isinstance(error, Exception):
        return
    await _deliver(
        lambda: observer.on_run_failed(conversation, error),
        lambda exc: logger.warning(
            "agent.run_observer.failure_delivery.degraded",
            agent_run_id=agent_run_id,
            exc_info=exc,
        ),
    )
