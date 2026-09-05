"""The schedule poller must actually start when the worker does.

Nothing else covered this. The poller is what fires every due schedule and every
timer in the product, and it used to be started by a line in
`app/core/infrastructure/jobs/streaq_runtime.py` — so "does it run" was answered,
if at all, by a worker booting in an e2e run.

It is a module worker lifespan now, and a lifespan that raises or quietly yields
without doing its work is invisible to every unit test that calls a handler
directly: a worker lifespan only runs when a worker starts. That is the shape of
the FastStream dependency-model bug, where a `Protocol` annotation bound at
worker startup stopped the worker booting and no unit test could see it.

So this enters the real lifespan the real way, through `enter_worker_lifespans`,
and asserts the task exists while the stack is open and is gone once it closes.

**No doubles.** The real `run_schedule_poller` runs: its loop swallows every
non-cancel exception per tick — deliberately, because "a poller that dies on a
transient database error stops every schedule in the fleet" — so a unit of work
that refuses to open costs one warning and a backoff sleep. A stand-in would
prove less and could drift from it.

Which claimers the poller is handed is left to `test_due_schedule_claimer_e2e.py`,
where a timer actually coming due is observable. Asserting it here would mean
patching the poller, which is a double inside this module's own subject.
"""

from __future__ import annotations

import asyncio
from contextlib import AsyncExitStack, asynccontextmanager
from types import SimpleNamespace

import pytest
from sqlalchemy.exc import SQLAlchemyError

from app.core.registry.assembly import enter_worker_lifespans
from app.modules.schedule.module import module as schedule_module

pytestmark = pytest.mark.unit


def _poller_tasks() -> list[asyncio.Task]:
    return [t for t in asyncio.all_tasks() if t.get_name() == "schedule-poller"]


def _worker_context() -> SimpleNamespace:
    """A worker context whose unit of work refuses to open.

    Both of this module's worker lifespans tolerate that by design: the breaker
    reconciliation swallows `SQLAlchemyError` so a worker booting ahead of its
    migrations still starts, and the poll loop treats a failed tick as a
    degraded warning. So this is enough to run both for real without a database.
    """

    @asynccontextmanager
    async def uow_factory():
        raise SQLAlchemyError("no database in a unit test")
        yield  # pragma: no cover - unreachable; keeps the generator form

    return SimpleNamespace(uow_factory=uow_factory)


@pytest.mark.asyncio
async def test_the_worker_lifespan_starts_and_stops_the_poller():
    assert _poller_tasks() == [], "a poller was already running before this test"

    async with AsyncExitStack() as stack:
        await enter_worker_lifespans(stack, [schedule_module], _worker_context())
        # Let the task reach its first await, so "started" means started.
        await asyncio.sleep(0)
        running = _poller_tasks()
        assert len(running) == 1, "the worker lifespan did not start the poller"
        assert not running[0].done()

    assert _poller_tasks() == [], "the poller outlived the lifespan that owns it"
