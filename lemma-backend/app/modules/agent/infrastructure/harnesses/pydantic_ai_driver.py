"""Owning the child task that drives pydantic-ai's graph.

The graph runs in a child task because its anyio cancel scopes are bound to the
task that created them: unwinding them anywhere else corrupts the scope stack
and takes the worker with it. That makes the parent the only place a stop can
be acted on, and this module is the parent's half -- how it waits, how it
notices a stop the driver cannot, and how it ends the driver afterwards.
"""

from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable
from uuid import UUID

import anyio

from app.core.log.log import get_logger

logger = get_logger(__name__)

# How often the consumer looks for a stop while the driver is busy. The stop
# checker behind this is itself throttled and sticky, so a shorter interval
# costs a dictionary lookup rather than a query.
STOP_POLL_SECONDS = 0.5

# Returned by `next_event_or_stop` when the stop won the race.
STOP_WHILE_BUSY = object()


async def next_event_or_stop(
    queue: "asyncio.Queue[tuple[str, object]]",
    stop_requested: Callable[[], Awaitable[bool]],
) -> "tuple[str, object] | object":
    """The next event from the driver, or the stop that outran it.

    Every stop check the driver makes sits between streamed chunks, so none of
    them runs while a tool call is executing -- and a workspace command is
    allowed to hold for five minutes. Waiting on the queue alone therefore made
    Stop do nothing at all for the length of the longest tool call in the run,
    while the client went on showing the run as live.

    An event that is already queued always wins: it has happened, and dropping
    it would leave the history missing something the run really did.
    """
    getter = asyncio.ensure_future(queue.get())
    try:
        while True:
            done, _pending = await asyncio.wait({getter}, timeout=STOP_POLL_SECONDS)
            if done:
                return getter.result()
            if await stop_requested():
                return STOP_WHILE_BUSY
    finally:
        if not getter.done():
            getter.cancel()


async def teardown_driver(task: "asyncio.Task[Any]", *, agent_run_id: UUID) -> bool:
    """End the driver task and let it unwind inside its own task.

    Returns whether we were the ones who cancelled it, which is what tells
    `reraise_driver_failure` that a relayed CancelledError is routine rather
    than the graph dying under a healthy parent.
    """
    cancelled_by_us = False
    if not task.done():
        cancelled_by_us = True
        task.cancel()
    # Shielded so our own cancellation does not abandon that cleanup.
    with anyio.CancelScope(shield=True):
        try:
            await task
        except (Exception, asyncio.CancelledError) as exc:
            report_teardown_failure(exc, agent_run_id=agent_run_id)
    return cancelled_by_us


def report_teardown_failure(exc: BaseException, *, agent_run_id: UUID) -> None:
    """Report a driver task that crashed unwinding — but not cancellation.

    Swallowed either way; ``reraise_driver_failure`` owns what the run reports.
    Caught by name, not as ``BaseException``, so SystemExit still propagates.
    """
    if isinstance(exc, asyncio.CancelledError):
        return
    logger.error(
        "agent.pydantic_ai.stream_teardown.failed",
        agent_run_id=str(agent_run_id),
        exc_info=exc,
    )
