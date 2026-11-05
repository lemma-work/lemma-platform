"""Recurring maintenance as periodic work items: what a ``@streaq_cron`` became.

``@periodic_job(kind, every=..., cls=...)`` registers a zero-argument coroutine
as a JOB kind and arms it as a periodic named after the kind: one item in the
``system`` namespace, armed at worker boot, re-added ``every`` after each run in
that run's completion transaction. So exactly one chain exists however many
workers there are, and a missed tick is impossible rather than recovered.

A tick that raises is an ordinary failed work item: the runtime logs it with
its traceback (``work.item.failed``), retries it with backoff, and after the
kind's attempts marks it ``dead`` (``lemma.work.dead``, kept for inspection).
Going dead does not stop the chain: ``lifecycle.mark_dead`` re-arms a periodic
``every`` later in the same transaction, so a bad tick waits for the next one,
as a failed cron did, instead of stopping the sweep until the next deploy.

There is no wall-clock alignment: a daily sweep runs 24 h after the previous
run (first run at the first worker boot that arms it), not at 04:00. For
retention and reconciliation that is fine; nothing here is a calendar.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import timedelta

from pydantic import BaseModel

from app.core.infrastructure.work.job import JobContext
from app.core.infrastructure.work.registry import WorkRegistry, registry
from app.core.infrastructure.work.types import WorkClass, WorkMode

#: What a streaq job got (``JOB_TIMEOUT_SECONDS``): a sweep is budgeted well
#: inside it, and one that is not is cancelled rather than left holding a slot.
DEFAULT_PERIODIC_MAX_RUNTIME = timedelta(minutes=30)

type PeriodicBody = Callable[[], Awaitable[object]]


class NoPayload(BaseModel):
    """A periodic carries nothing: it always means "do the sweep now"."""


def periodic_job(
    kind: str,
    *,
    every: timedelta,
    cls: WorkClass = WorkClass.ADMIN,
    max_runtime: timedelta = DEFAULT_PERIODIC_MAX_RUNTIME,
    work_registry: WorkRegistry | None = None,
) -> Callable[[PeriodicBody], PeriodicBody]:
    """Run the decorated coroutine every ``every``, as kind (and periodic) ``kind``.

    ``cls`` is ``ADMIN`` for retention and housekeeping, ``AUTOMATION`` for a
    check that talks to the outside world. Returns the body unchanged, so a
    test calls it directly as before.
    """
    target = work_registry or registry

    def register(body: PeriodicBody) -> PeriodicBody:
        async def run(ctx: JobContext, payload: NoPayload) -> None:
            del ctx, payload
            await body()

        run.__name__ = run.__qualname__ = f"periodic_{body.__name__}"
        target.handler(
            kind=kind,
            cls=cls,
            mode=WorkMode.JOB,
            payload=NoPayload,
            max_runtime=max_runtime,
        )(run)
        target.periodic(kind, every, kind)
        return body

    return register


__all__ = ["DEFAULT_PERIODIC_MAX_RUNTIME", "NoPayload", "periodic_job"]
