"""Function worker and queue-reconciliation invariants."""

from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.core.infrastructure.jobs.streaq_runtime import AppWorkerContext, streaq_worker
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


def test_worker_function_service_composition_matches_current_constructor(
    monkeypatch,
) -> None:
    storage_factory = object()
    monkeypatch.setattr(
        AppWorkerContext,
        "build_function_storage_factory",
        lambda self: storage_factory,
    )
    context = AppWorkerContext(
        job_queue=AsyncMock(),
        uow_factory=AsyncMock(),
    )

    service = context.build_function_service(
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
