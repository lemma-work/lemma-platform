"""Function worker and queue-reconciliation invariants."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.function.config import function_settings
from app.core.infrastructure.jobs.streaq_runtime import streaq_worker
from app.modules.function.api import dependencies
from app.modules.function.events import handlers
from app.modules.function.domain.errors import FunctionRunQueueUnavailable
from app.modules.function.domain.identities import function_run_job_id
from app.modules.function.infrastructure.function_run_queue import (
    StreaqFunctionRunQueue,
)


@pytest.mark.asyncio
async def test_function_queue_uses_run_id_as_its_only_queue_identity() -> None:
    run_id = uuid4()
    raw_queue = AsyncMock()
    queue = StreaqFunctionRunQueue(raw_queue)

    result = await queue.enqueue(run_id)

    assert result == function_run_job_id(run_id)
    raw_queue.enqueue.assert_awaited_once_with(
        "process_function_run",
        run_id=str(run_id),
        _job_id=f"function:{run_id}",
    )


@pytest.mark.asyncio
async def test_function_queue_translates_transport_failure() -> None:
    raw_queue = AsyncMock()
    raw_queue.enqueue.side_effect = ConnectionError("redis unavailable")
    queue = StreaqFunctionRunQueue(raw_queue)

    with pytest.raises(FunctionRunQueueUnavailable):
        await queue.enqueue(uuid4())


@pytest.mark.asyncio
async def test_reconcile_does_not_hold_db_connection_during_queue_io(
    monkeypatch,
) -> None:
    run_id = uuid4()
    state = {"open": False, "opens": 0}

    class _UowFactory:
        @asynccontextmanager
        async def __call__(self):
            state["open"] = True
            state["opens"] += 1
            try:
                yield "uow"
            finally:
                state["open"] = False

    class _Repository:
        def __init__(self, uow):
            assert uow == "uow"

        async def list_pending_async_runs(self, *, now, limit):
            assert state["open"] is True
            assert limit == 100
            return [run_id]

    class _Queue:
        def __init__(self, raw):
            pass

        async def enqueue(self, received_run_id):
            assert state["open"] is False
            assert received_run_id == run_id
            return f"function:{run_id}"

    monkeypatch.setattr(handlers, "FunctionRunRepository", _Repository)
    monkeypatch.setattr(handlers, "StreaqFunctionRunQueue", _Queue)
    monkeypatch.setattr(handlers, "get_streaq_job_queue", lambda: object())

    marked = await handlers._reconcile_unqueued_function_runs(
        uow_factory=_UowFactory(),
        now=datetime.now(timezone.utc),
    )

    assert marked == 1
    assert state == {"open": False, "opens": 1}


def test_function_worker_and_reconciler_are_registered() -> None:
    assert "process_function_run" in streaq_worker.registry
    assert "reconcile_function_runs" in streaq_worker.registry
    assert "prune_function_runs" in streaq_worker.registry


class _CountingUowFactory:
    @asynccontextmanager
    async def __call__(self):
        yield "uow"


def _retention_repository(batches: list[int], seen: list[dict]):
    class _Repository:
        def __init__(self, uow):
            assert uow == "uow"

        async def delete_terminal_before(self, *, cutoff, batch_size):
            seen.append({"cutoff": cutoff, "batch_size": batch_size})
            return batches.pop(0)

    return _Repository


@pytest.mark.asyncio
async def test_run_retention_drains_a_backlog_larger_than_one_batch(
    monkeypatch,
) -> None:
    """The point of the sweep: a backlog has to clear, not tick down by one batch."""

    monkeypatch.setattr(function_settings, "function_run_retention_batch_size", 10)
    monkeypatch.setattr(
        function_settings, "function_run_retention_budget_seconds", 60.0
    )
    monkeypatch.setattr(function_settings, "function_run_retention_days", 30)
    seen: list[dict] = []
    monkeypatch.setattr(
        handlers, "FunctionRunRepository", _retention_repository([10, 10, 3], seen)
    )
    monkeypatch.setattr(handlers, "provide_uow_factory", _CountingUowFactory)

    await handlers._prune_function_runs()

    assert len(seen) == 3
    assert all(call["batch_size"] == 10 for call in seen)
    # Every batch measures against the same cutoff, so a long drain cannot
    # walk its own window forward and start deleting rows inside retention.
    assert len({call["cutoff"] for call in seen}) == 1


@pytest.mark.asyncio
async def test_run_retention_stops_when_its_budget_is_spent(monkeypatch) -> None:

    monkeypatch.setattr(function_settings, "function_run_retention_batch_size", 10)
    monkeypatch.setattr(function_settings, "function_run_retention_budget_seconds", 5.0)
    monkeypatch.setattr(function_settings, "function_run_retention_days", 30)
    seen: list[dict] = []
    monkeypatch.setattr(
        handlers, "FunctionRunRepository", _retention_repository([10] * 50, seen)
    )
    monkeypatch.setattr(handlers, "provide_uow_factory", _CountingUowFactory)
    clock = iter([0.0, 1.0, 99.0] + [99.0] * 50)
    monkeypatch.setattr(handlers.time, "monotonic", lambda: next(clock))

    await handlers._prune_function_runs()

    assert len(seen) == 2


@pytest.mark.asyncio
async def test_a_zero_retention_budget_disables_the_sweep(monkeypatch) -> None:

    monkeypatch.setattr(function_settings, "function_run_retention_budget_seconds", 0.0)
    seen: list[dict] = []
    monkeypatch.setattr(
        handlers, "FunctionRunRepository", _retention_repository([10], seen)
    )
    monkeypatch.setattr(handlers, "provide_uow_factory", _CountingUowFactory)

    await handlers._prune_function_runs()

    assert seen == []


@pytest.mark.asyncio
async def test_a_failed_cron_tick_is_logged_not_raised(monkeypatch) -> None:
    """A raising tick must not take the schedule down with it."""

    async def _boom() -> None:
        raise RuntimeError("database unavailable")

    await handlers._guard_cron("prune_function_runs", _boom())


def test_worker_function_service_composition_matches_current_constructor(
    monkeypatch,
) -> None:
    storage_factory = object()
    monkeypatch.setattr(
        dependencies,
        "get_function_storage_factory",
        lambda: storage_factory,
    )

    service = dependencies.build_function_service(
        SimpleNamespace(session=SimpleNamespace(), set_message_bus=lambda bus: None)
    )

    assert service.storage_factory is storage_factory


@pytest.mark.asyncio
async def test_worker_error_fallback_terminalizes_the_same_run(monkeypatch) -> None:
    run_id = uuid4()
    failed: list[tuple[object, str]] = []

    class _Repository:
        def __init__(self, uow):
            assert uow == "uow"

        async def fail_unfinished(self, received_run_id, *, error):
            failed.append((received_run_id, error))

    class _WorkerContext:
        @asynccontextmanager
        async def uow(self):
            yield "uow"

    monkeypatch.setattr(handlers, "FunctionExecutionRepository", _Repository)

    await handlers._fail_run_after_worker_error(
        _WorkerContext(),
        run_id,
        RuntimeError("lost runtime response"),
    )

    assert failed == [(run_id, "Function execution failed (RuntimeError)")]
