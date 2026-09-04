"""Printing every pending coroutine's stack, on demand, from a wedged worker.

Startup housekeeping the lane runner installs, kept beside `cron_pruning` for
the same reason: neither is about running tasks, and both were making the file
that *is* about running tasks longer than it should be.
"""

from __future__ import annotations

import asyncio
import traceback

from app.core.log.log import get_logger

logger = get_logger(__name__)


def install_task_dump_handler() -> None:
    """Print every pending coroutine's stack on SIGQUIT.

    A worker that stops responding to SIGTERM shows nothing useful in a thread
    dump: `faulthandler` reports the event loop sitting in `select()`, which is
    what an idle loop always looks like. The question is always *which awaited
    coroutine is not finishing*, and only the task list answers it. SIGQUIT is
    free -- neither streaq nor anything else here uses it.

    Windows has no SIGQUIT at all, so `signal.SIGQUIT` raises `AttributeError`
    there rather than the errors the guard below anticipates. That was harmless
    while only `python -m app.worker` reached here, because Desktop never ran
    it; the moment the embedded app runs its lanes, this is the first thing a
    Windows backend would execute, and it would fail before serving anything.
    """
    import signal

    sigquit = getattr(signal, "SIGQUIT", None)
    if sigquit is None:  # pragma: no cover - platform
        return

    def _dump(*_args: object) -> None:
        for task in asyncio.all_tasks():
            frames = "".join(
                traceback.format_stack(task.get_coro().cr_frame)  # type: ignore[union-attr]
                if getattr(task.get_coro(), "cr_frame", None)
                else []
            )
            logger.warning(
                "infrastructure.streaq_runtime.pending_task_dump.diagnostic",
                task_name=task.get_name(),
                frames=frames[-2000:],
            )

    try:
        asyncio.get_running_loop().add_signal_handler(sigquit, _dump)
    except NotImplementedError, RuntimeError:  # pragma: no cover - platform
        pass
