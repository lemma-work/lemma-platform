"""Function worker and queue-reconciliation invariants."""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from app.modules.function.config import function_settings
from app.core.infrastructure.jobs.streaq_runtime import streaq_worker
from app.modules.function.api import dependencies
from app.modules.function.events import handlers


def test_only_retention_crons_remain_on_streaq() -> None:
    """Runs and their deadlines are work items; nothing re-enqueues them."""
    assert "prune_function_runs" in streaq_worker.registry
    assert "process_function_run" not in streaq_worker.registry
    assert "reconcile_function_runs" not in streaq_worker.registry


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
