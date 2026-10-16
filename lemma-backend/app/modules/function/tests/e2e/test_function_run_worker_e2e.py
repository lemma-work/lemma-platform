"""A real worker killed mid ``function.run``: the run is invoked once, and ends.

SIGKILL is the case nothing can clean up after: no handler runs, the item stays
``running`` under a lease nobody renews. The next worker reaps the lease and
claims the item again -- and what it does then depends only on the run row:

* killed before the start (the sandbox was still coming up): the run is still
  PENDING, so the next worker starts it, and it is invoked exactly once;
* killed after the start, mid-invocation: the run is RUNNING, so the next
  worker leaves it alone -- it is never invoked twice -- and its
  ``function.deadline`` item fails it when no result arrives.

The lease is aged rather than waited out: an expired lease is all the reaper
looks at, and waiting a minute for it would prove nothing more.
"""

from __future__ import annotations

import os
import signal
from collections.abc import AsyncIterator
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, text, update

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.function.infrastructure.models import FunctionRunModel
from app.modules.function.tests.e2e.function_work_support import (
    FakeRuntime,
    create_queued_run,
    fake_runtime,
    seed_function,
)
from app.modules.test_support.e2e.waiters import eventually
from app.modules.test_support.e2e.worker_process import production_worker_process

# Slow, and in the protected lane: each starts its own worker subprocess, which
# must not share an e2e process with the session-scoped base worker (both
# would claim the same work items, and the harness flushes the shared Redis).
pytestmark = [pytest.mark.e2e, pytest.mark.slow]


@pytest.fixture
async def runtime() -> AsyncIterator[FakeRuntime]:
    async with fake_runtime() as served:
        yield served


def _worker(e2e_settings, runtime: FakeRuntime, name: str):
    return production_worker_process(
        e2e_settings,
        log_prefix=name,
        worker_entrypoint="app.modules.function.tests.e2e.function_work_entrypoint",
        extra_env={
            "E2E_FAKE_FUNCTION_RUNTIME_URL": runtime.url,
            "WORK_POLL_INTERVAL_SECONDS": "0.2",
        },
    )


async def _queue(db_manager, pod_id: UUID, user_id: UUID, mode: str) -> UUID:
    async with db_manager.session_factory() as session:
        function = await seed_function(
            session, pod_id=pod_id, user_id=user_id, mode=mode
        )
        uow = SqlAlchemyUnitOfWork(session)
        run = await create_queued_run(uow, function_id=function.id, user_id=user_id)
        await uow.commit()
    assert run.id is not None
    return run.id


async def _scalar(db_manager, sql: str, **params: object) -> object:
    async with db_manager.session_factory() as session:
        return await session.scalar(text(sql), params)


async def _expire_leases(db_manager) -> None:
    async with db_manager.session_factory() as session:
        await session.execute(
            text(
                "UPDATE work_items SET lease_until = now() - interval '1 second' "
                "WHERE state = 'running'"
            )
        )
        await session.commit()


async def _run_status(db_manager, run_id: UUID) -> str:
    return str(
        await _scalar(
            db_manager, "SELECT status FROM function_runs WHERE id = :id", id=run_id
        )
    )


async def test_a_worker_killed_before_the_start_leaves_the_run_to_the_next_one(
    e2e_settings, db_manager, test_pod, fixed_test_user, runtime: FakeRuntime
) -> None:
    runtime.hold_prepare = True  # a cold sandbox: the start has not happened
    run_id = await _queue(
        db_manager, UUID(test_pod["id"]), UUID(fixed_test_user["id"]), "accept"
    )

    async with _worker(e2e_settings, runtime, "fn_kill_before") as first:
        await eventually(
            label="the first worker is waiting for the sandbox",
            probe=lambda: _scalar(
                db_manager,
                "SELECT count(*) FROM work_items WHERE state = 'running' AND kind = 'function.run'",
            ),
            done=lambda n: n == 1 and runtime.prepares >= 1,
        )
        os.kill(first.process.pid, signal.SIGKILL)
        first.process.wait(timeout=10)
    assert await _run_status(db_manager, run_id) == "PENDING"

    runtime.hold_prepare = False
    runtime.release()
    await _expire_leases(db_manager)
    async with _worker(e2e_settings, runtime, "fn_kill_before_2"):
        await eventually(
            label="the next worker started the run",
            probe=lambda: _run_status(db_manager, run_id),
            done=lambda status: status == "RUNNING",
        )
        await eventually(
            label="its item is done",
            probe=lambda: _scalar(
                db_manager,
                "SELECT count(*) FROM work_items WHERE kind = 'function.run'",
            ),
            done=lambda n: n == 0,
        )
    assert runtime.invocations[str(run_id)] == 1
    assert (
        await _scalar(
            db_manager,
            "SELECT lease_losses FROM work_items WHERE kind = 'function.run'",
        )
        is None
    )


async def test_a_worker_killed_mid_invocation_never_invokes_twice_and_the_deadline_ends_it(
    e2e_settings, db_manager, test_pod, fixed_test_user, runtime: FakeRuntime
) -> None:
    run_id = await _queue(
        db_manager, UUID(test_pod["id"]), UUID(fixed_test_user["id"]), "hold"
    )

    async with _worker(e2e_settings, runtime, "fn_kill_after") as first:
        await eventually(
            label="the invocation is in flight",
            probe=lambda: _run_status(db_manager, run_id),
            done=lambda status: status == "RUNNING" and runtime.in_flight == 1,
        )
        os.kill(first.process.pid, signal.SIGKILL)
        first.process.wait(timeout=10)

    await _expire_leases(db_manager)
    async with _worker(e2e_settings, runtime, "fn_kill_after_2"):
        await eventually(
            label="the reclaimed item finished without invoking",
            probe=lambda: _scalar(
                db_manager,
                "SELECT count(*) FROM work_items WHERE kind = 'function.run'",
            ),
            done=lambda n: n == 0,
        )
        assert runtime.invocations[str(run_id)] == 1
        assert await _run_status(db_manager, run_id) == "RUNNING"

        # No result will come. Its window closes (aged, not waited out) and
        # the deadline item, already written by the start, ends it.
        async with db_manager.session_factory() as session:
            await session.execute(
                update(FunctionRunModel)
                .where(FunctionRunModel.id == run_id)
                .values(deadline_at=func.now() - timedelta(minutes=5))
            )
            await session.execute(
                text(
                    "UPDATE work_items SET run_at = now() WHERE kind = 'function.deadline'"
                )
            )
            await session.commit()
        await eventually(
            label="the deadline failed the run",
            probe=lambda: _run_status(db_manager, run_id),
            done=lambda status: status == "FAILED",
        )
    assert runtime.invocations[str(run_id)] == 1
    error = await _scalar(
        db_manager, "SELECT error FROM function_runs WHERE id = :id", id=run_id
    )
    assert "deadline exceeded" in str(error)
