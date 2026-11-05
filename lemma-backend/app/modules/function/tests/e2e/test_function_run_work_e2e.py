"""Queued function runs on the work runtime, against real Postgres.

Each run goes through the shipping path: the run row and its ``function.run``
item are written in one transaction, the runtime claims the item, the
dispatcher starts the run under the pod's gate and invokes a runtime on
loopback (``function_work_support.py``), and every terminal transition emits
``FunctionRunFinished`` in its own transaction. Only the provider's endpoint
lease is replaced.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import timedelta
from uuid import UUID

import pytest
from sqlalchemy import func, select, text, update

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.work import RetryPolicy, WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import (
    dispatcher_for,
    make_due,
    run_until_idle,
)
from app.modules.function.domain.entities import FunctionRunStatus
from app.modules.function.events.run_deadline import register_function_deadline
from app.modules.function.events.run_work import register_function_run_work
from app.modules.function.infrastructure.models import FunctionModel, FunctionRunModel
from app.modules.function.tests.e2e.function_work_support import (
    FakeRuntime,
    callback_settler,
    create_queued_run,
    fake_runtime,
    loopback_dispatcher_factory,
    seed_function,
)
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

CAP = 3


@dataclass
class Harness:
    runtime: FakeRuntime
    registry: WorkRegistry
    sessions: object
    pod_id: UUID
    user_id: UUID

    async def function(self, mode: str, *, pod_id: UUID | None = None) -> UUID:
        async with self.sessions() as session:  # type: ignore[operator]
            function = await seed_function(
                session, pod_id=pod_id or self.pod_id, user_id=self.user_id, mode=mode
            )
            await session.commit()
            return function.id

    async def queue(self, function_id: UUID, count: int = 1) -> list[UUID]:
        async with self.sessions() as session:  # type: ignore[operator]
            uow = SqlAlchemyUnitOfWork(session)
            runs = [
                await create_queued_run(
                    uow, function_id=function_id, user_id=self.user_id
                )
                for _ in range(count)
            ]
            await uow.commit()
        return [run.id for run in runs if run.id is not None]

    async def drain(self) -> int:
        return await run_until_idle(
            self.sessions,  # type: ignore[arg-type]
            registry=self.registry,
            classes=[WorkClass.AUTOMATION, WorkClass.EVENTS],
        )

    async def scalar(self, sql: str, **params: object) -> object:
        async with self.sessions() as session:  # type: ignore[operator]
            return await session.scalar(text(sql), params)

    async def statuses(self, run_ids: list[UUID]) -> dict[UUID, str]:
        async with self.sessions() as session:  # type: ignore[operator]
            rows = await session.execute(
                select(FunctionRunModel.id, FunctionRunModel.status).where(
                    FunctionRunModel.id.in_(run_ids)
                )
            )
            return {run_id: str(status) for run_id, status in rows.all()}


@pytest.fixture
async def harness(db_manager, test_pod, fixed_test_user) -> AsyncIterator[Harness]:
    async with fake_runtime() as runtime:
        runtime.on_accepted = callback_settler(db_manager.session_factory)
        registry = WorkRegistry()
        register_function_run_work(
            registry,
            dispatcher_factory=loopback_dispatcher_factory(runtime.url, cap=CAP),
            retry=RetryPolicy(max_attempts=3, unavailable_budget=timedelta(hours=1)),
        )
        register_function_deadline(registry)
        yield Harness(
            runtime,
            registry,
            db_manager.session_factory,
            UUID(test_pod["id"]),
            UUID(fixed_test_user["id"]),
        )


async def test_a_run_and_the_item_that_starts_it_commit_together_or_not_at_all(
    harness: Harness,
) -> None:
    function_id = await harness.function("complete")
    async with harness.sessions() as session:  # type: ignore[operator]
        uow = SqlAlchemyUnitOfWork(session)
        rolled_back = await create_queued_run(
            uow, function_id=function_id, user_id=harness.user_id
        )
        await uow.rollback()
    (committed,) = await harness.queue(function_id)

    items = await harness.scalar(
        "SELECT string_agg(payload->>'run_id', ',') FROM work_items "
        "WHERE kind = 'function.run'"
    )
    assert items == str(committed)
    assert str(rolled_back.id) not in str(items)


async def test_a_queued_run_starts_completes_and_announces_its_end(
    harness: Harness,
) -> None:
    import app.modules.workflow.events.run_work  # noqa: F401  the subscriber

    function_id = await harness.function("complete")
    (run_id,) = await harness.queue(function_id)

    await harness.drain()
    await eventually(
        label="the callback settled the run",
        probe=lambda: harness.statuses([run_id]),
        done=lambda rows: rows.get(run_id) == "COMPLETED",
    )

    assert harness.runtime.invocations[str(run_id)] == 1
    assert (
        await harness.scalar(
            "SELECT count(*) FROM work_items WHERE kind = 'function.run'"
        )
        == 0
    )
    # Its end was announced in the transaction that ended it: whatever waits
    # on the run has its work item already.
    assert (
        await harness.scalar(
            "SELECT count(*) FROM work_items WHERE kind = 'workflow.resume' "
            "AND payload->>'function_run_id' = :run",
            run=str(run_id),
        )
        == 1
    )
    # The start wrote the deadline item that ends the run if nothing else does.
    assert (
        await harness.scalar(
            "SELECT count(*) FROM work_items WHERE kind = 'function.deadline' "
            "AND payload->>'run_id' = :run",
            run=str(run_id),
        )
        == 1
    )


async def test_the_pod_cap_holds_under_concurrent_dispatchers_and_costs_nothing(
    harness: Harness,
) -> None:
    """Never more than the cap RUNNING; the rest wait their turn uncharged."""
    harness.runtime.on_accepted = None
    function_id = await harness.function("hold")
    run_ids = await harness.queue(function_id, count=10)
    dispatchers = [
        dispatcher_for(
            harness.sessions,  # type: ignore[arg-type]
            WorkClass.AUTOMATION,
            registry=harness.registry,
            owner=f"d{index}",
            slots=10,
            quantum=10,
        )
        for index in range(3)
    ]
    claimed = await asyncio.gather(*(d.claim_once() for d in dispatchers))
    pending = [
        asyncio.create_task(d.execute(item))
        for d, items in zip(dispatchers, claimed, strict=True)
        for item in items
    ]

    async def running() -> int:
        return int(
            await harness.scalar(  # type: ignore[arg-type]
                "SELECT count(*) FROM function_runs WHERE status = 'RUNNING'"
            )
        )

    await eventually(
        label="the cap's worth of runs reached the runtime",
        probe=lambda: asyncio.sleep(0, harness.runtime.in_flight),
        done=lambda n: n == CAP,
    )
    # Everything claimed and not started has settled back to the queue.
    await eventually(
        label="the turned-away items were deferred",
        probe=lambda: harness.scalar(
            "SELECT count(*) FROM work_items WHERE kind = 'function.run' "
            "AND state = 'queued'"
        ),
        done=lambda n: n == 10 - CAP,
    )
    assert await running() == CAP
    assert (
        await harness.scalar(
            "SELECT coalesce(max(attempts), 0) FROM work_items WHERE kind = 'function.run'"
        )
        == 0
    ), "a deferral is never charged"

    # The slots are held by the RUNNING rows: they free as runs end.
    async with harness.sessions() as session:  # type: ignore[operator]
        await session.execute(
            update(FunctionRunModel)
            .where(FunctionRunModel.status == FunctionRunStatus.RUNNING)
            .values(status=FunctionRunStatus.COMPLETED)
        )
        await session.commit()
    harness.runtime.on_accepted = callback_settler(harness.sessions)
    harness.runtime.release()
    await asyncio.gather(*pending)

    async def remaining() -> int:
        await make_due(harness.sessions, kind="function.run")  # type: ignore[arg-type]
        await harness.drain()
        assert await running() <= CAP
        return int(
            await harness.scalar(  # type: ignore[arg-type]
                "SELECT count(*) FROM work_items WHERE kind = 'function.run'"
            )
        )

    await eventually(
        label="every run was started, a cap's worth at a time",
        probe=remaining,
        done=lambda n: n == 0,
    )
    assert harness.runtime.max_in_flight <= CAP
    assert all(harness.runtime.invocations[str(r)] == 1 for r in run_ids)


async def test_a_job_whose_callback_never_comes_is_failed_by_its_deadline(
    harness: Harness,
) -> None:
    function_id = await harness.function("accept")
    (run_id,) = await harness.queue(function_id)
    await harness.drain()
    assert (await harness.statuses([run_id]))[run_id] == "RUNNING"

    # Not yet: the deadline item re-arms for the run's real deadline.
    await make_due(harness.sessions, kind="function.deadline")  # type: ignore[arg-type]
    await harness.drain()
    assert (await harness.statuses([run_id]))[run_id] == "RUNNING"

    async with harness.sessions() as session:  # type: ignore[operator]
        await session.execute(
            update(FunctionRunModel)
            .where(FunctionRunModel.id == run_id)
            .values(deadline_at=func.now() - timedelta(minutes=5))
        )
        await session.commit()
    await make_due(harness.sessions, kind="function.deadline")  # type: ignore[arg-type]
    await harness.drain()

    async with harness.sessions() as session:  # type: ignore[operator]
        run = await session.get(FunctionRunModel, run_id)
    assert run is not None
    assert run.status == FunctionRunStatus.FAILED
    assert "deadline exceeded" in (run.error or "")
    assert harness.runtime.invocations[str(run_id)] == 1


async def test_an_unreachable_sandbox_is_waited_out_without_charging_the_run(
    harness: Harness,
) -> None:
    harness.runtime.unavailable = True
    function_id = await harness.function("complete")
    (run_id,) = await harness.queue(function_id)

    await harness.drain()

    assert (await harness.statuses([run_id]))[run_id] == "PENDING"
    row = await harness.scalar(
        "SELECT attempts || ':' || (first_unavailable_at IS NOT NULL)::text "
        "FROM work_items WHERE kind = 'function.run'"
    )
    assert row == "0:true"
    assert harness.runtime.invocations[str(run_id)] == 0

    harness.runtime.unavailable = False
    await make_due(harness.sessions, kind="function.run")  # type: ignore[arg-type]
    await harness.drain()
    await eventually(
        label="the run completed once the sandbox was up",
        probe=lambda: harness.statuses([run_id]),
        done=lambda rows: rows.get(run_id) == "COMPLETED",
    )
    assert harness.runtime.invocations[str(run_id)] == 1


async def test_a_runtime_error_fails_the_run_and_is_never_retried(
    harness: Harness,
) -> None:
    function_id = await harness.function("fail")
    (run_id,) = await harness.queue(function_id)

    await harness.drain()
    await eventually(
        label="the run failed",
        probe=lambda: harness.statuses([run_id]),
        done=lambda rows: rows.get(run_id) == "FAILED",
    )
    await make_due(harness.sessions)  # type: ignore[arg-type]
    await harness.drain()

    assert harness.runtime.invocations[str(run_id)] == 1
    assert (
        await harness.scalar(
            "SELECT count(*) FROM work_items WHERE kind = 'function.run'"
        )
        == 0
    )


async def test_a_claim_after_the_start_never_invokes_again(harness: Harness) -> None:
    """The lease of a worker that died after starting the run expires; the
    next claim finds the run RUNNING and leaves it to its callback or deadline."""
    harness.runtime.on_accepted = None
    function_id = await harness.function("accept")
    (run_id,) = await harness.queue(function_id)
    await harness.drain()
    assert harness.runtime.invocations[str(run_id)] == 1

    # As if that worker had died before deleting its item: the item is back.
    async with harness.sessions() as session:  # type: ignore[operator]
        await session.execute(
            text(
                "INSERT INTO work_items (id, ns, class, kind, payload) VALUES "
                "(gen_random_uuid(), :ns, 'automation', 'function.run', "
                "jsonb_build_object('run_id', CAST(:run AS text)))"
            ),
            {"ns": f"pod:{harness.pod_id}", "run": str(run_id)},
        )
        await session.commit()
    await harness.drain()

    assert harness.runtime.invocations[str(run_id)] == 1
    assert (await harness.statuses([run_id]))[run_id] == "RUNNING"


async def test_one_pod_launching_thousands_of_runs_does_not_hold_up_another(
    harness: Harness, fixed_test_org, authenticated_client
) -> None:
    """Another pod's runs are claimed in the very first round."""
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": "Quiet pod",
            "slug": f"quiet-{harness.pod_id.hex[:8]}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
        follow_redirects=True,
    )
    assert response.status_code == 201, response.text
    quiet_pod = UUID(response.json()["id"])
    noisy = await harness.function("complete")
    quiet = await harness.function("complete", pod_id=quiet_pod)
    async with harness.sessions() as session:  # type: ignore[operator]
        await session.execute(
            text(
                "INSERT INTO function_runs (id, function_id, revision_hash, user_id, "
                "input_data, status, deadline_at, job_id, created_at, updated_at) "
                "SELECT gen_random_uuid(), :fn, :rev, :user, '{}'::jsonb, 'PENDING', "
                "now() + interval '1 hour', 'bulk', now(), now() "
                "FROM generate_series(1, 2000)"
            ),
            {
                "fn": noisy,
                "rev": f"sha256:{'5' * 64}",
                "user": harness.user_id,
            },
        )
        await session.execute(
            text(
                "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key) "
                "SELECT gen_random_uuid(), :ns, 'automation', 'function.run', "
                "jsonb_build_object('run_id', r.id::text), 'function-run:' || r.id "
                "FROM function_runs r WHERE r.function_id = :fn"
            ),
            {"ns": f"pod:{harness.pod_id}", "fn": noisy},
        )
        await session.execute(
            text(
                "INSERT INTO work_queues (class, ns, max_running) "
                "VALUES ('automation', :ns, 32) ON CONFLICT DO NOTHING"
            ),
            {"ns": f"pod:{harness.pod_id}"},
        )
        await session.commit()
    quiet_runs = await harness.queue(quiet, count=3)

    dispatcher = dispatcher_for(
        harness.sessions,  # type: ignore[arg-type]
        WorkClass.AUTOMATION,
        registry=harness.registry,
        slots=8,
        quantum=2,
    )
    first_round = await dispatcher.claim_once()
    claimed = {UUID(item.payload["run_id"]) for item in first_round}
    await asyncio.gather(*(dispatcher.execute(item) for item in first_round))

    # Round-robin by namespace, quantum two: the quiet pod is served at once,
    # not behind two thousand of the noisy pod's runs.
    assert len(claimed & set(quiet_runs)) >= 2
    assert claimed - set(quiet_runs), "the noisy pod is served too"


async def test_runs_deleted_with_their_function_are_dead_not_retried(
    harness: Harness,
) -> None:
    function_id = await harness.function("complete")
    (run_id,) = await harness.queue(function_id)
    async with harness.sessions() as session:  # type: ignore[operator]
        await session.execute(
            text("DELETE FROM function_runs WHERE id = :run"), {"run": run_id}
        )
        await session.delete(await session.get(FunctionModel, function_id))
        await session.commit()

    await harness.drain()

    assert (
        await harness.scalar(
            "SELECT error_class FROM work_items WHERE kind = 'function.run'"
        )
        == "permanent"
    )
