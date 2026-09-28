"""Tell a cancellation aimed at this task from one that leaked in from another.

``asyncio.CancelledError`` means "stop", and the rule in this codebase is to let
it propagate (``docs/development.md``). That rule assumes the error was aimed
at the task that sees it. It is not always. anyio's ``TaskGroup.start`` hands a
child's failure back to whoever called ``start`` -- including a
``CancelledError`` the child got because *its* host scope was cancelled. A
client whose connection pool is a task group bound to some other task therefore
raises ``CancelledError`` into every caller once that other task's scope has
been cancelled, although none of those callers was ever cancelled.

Letting that propagate is not harmless. FastStream's subscriber supervisor
restarts a reader task that raised, *unless* the task ended cancelled, so a
stream consumer that lets one of these through stops reading for the rest of
the process. Every health signal stays green while it does. That is the
incident this module exists for: the ``datastore-file-events`` lane stopped for
hours on a ``CancelledError`` from coredis's ``connect_tcp`` while nothing had
cancelled it.

The distinguishing fact is ``Task.cancelling()``. A real cancellation of the
current task increments it before the error is delivered; a leaked one does
not. A leaked one is a failure of whatever was being called, and is converted
into :class:`StrayCancellationError` so it takes the ordinary error path --
retried, counted, dead-lettered when it keeps happening.
"""

from __future__ import annotations

import asyncio


class StrayCancellationError(RuntimeError):
    """A ``CancelledError`` reached a task that nobody had cancelled.

    An ordinary ``Exception`` on purpose: every retry, quarantine and
    supervisor path in the event transport handles ``Exception`` and
    deliberately lets ``BaseException`` through.
    """


def is_stray_cancellation(error: BaseException) -> bool:
    """True when ``error`` is a ``CancelledError`` the current task was not sent.

    Outside a task (no running loop, or a callback) there is nothing to compare
    against, so the answer is the conservative one: treat it as genuine.
    """
    if not isinstance(error, asyncio.CancelledError):
        return False
    try:
        task = asyncio.current_task()
    except RuntimeError:
        return False
    return task is not None and task.cancelling() == 0


def as_stray_cancellation(error: asyncio.CancelledError) -> StrayCancellationError:
    """The ordinary exception to raise in place of a stray cancellation."""
    stray = StrayCancellationError(
        f"CancelledError reached a task that was not cancelled: {error}"
    )
    stray.__cause__ = error
    return stray
