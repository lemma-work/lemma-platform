from __future__ import annotations

import asyncio
import logging

import pytest

from agentbox import maintenance, reconciliation
from agentbox.api.app import _reconcile_before_serving


async def _stop_after_first_pass(_seconds: float) -> None:
    raise asyncio.CancelledError


@pytest.mark.asyncio
async def test_cleanup_loop_reports_unexpected_failure(monkeypatch, caplog) -> None:
    class FailingWorker:
        async def run_once(self, *, deadline_at):
            del deadline_at
            raise RuntimeError("CANARY provider response")

    monkeypatch.setattr(maintenance.asyncio, "sleep", _stop_after_first_pass)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(asyncio.CancelledError):
            await maintenance.maintenance_loop(
                FailingWorker(),
                interval_seconds=1,
                operation_timeout_seconds=1,
            )

    record = next(
        item for item in caplog.records if item.msg == "agentbox.cleanup.failed"
    )
    assert record.lemma_fields["error_type"] == "RuntimeError"
    assert len(record.lemma_fields["error_stack_hash"]) == 64
    assert "CANARY" not in repr(record.lemma_fields)


@pytest.mark.asyncio
async def test_reconcile_loop_reports_unexpected_failure(monkeypatch, caplog) -> None:
    class FailingReconciler:
        async def reconcile_once(self, *, deadline_at):
            del deadline_at
            raise RuntimeError("CANARY provider response")

    monkeypatch.setattr(reconciliation.asyncio, "sleep", _stop_after_first_pass)

    with caplog.at_level(logging.WARNING):
        with pytest.raises(asyncio.CancelledError):
            await reconciliation.reconciliation_loop(
                FailingReconciler(),
                interval_seconds=1,
                operation_timeout_seconds=1,
            )

    record = next(
        item for item in caplog.records if item.msg == "agentbox.reconcile.failed"
    )
    assert record.lemma_fields["error_type"] == "RuntimeError"
    assert len(record.lemma_fields["error_stack_hash"]) == 64
    assert "CANARY" not in repr(record.lemma_fields)


@pytest.mark.asyncio
async def test_initial_reconcile_failure_does_not_block_serving(caplog) -> None:
    class FailingReconciler:
        async def reconcile_once(self, *, deadline_at):
            del deadline_at
            raise RuntimeError("CANARY provider response")

    with caplog.at_level(logging.WARNING):
        await _reconcile_before_serving(
            FailingReconciler(),
            operation_timeout_seconds=1,
        )

    record = next(
        item for item in caplog.records if item.msg == "agentbox.reconcile.failed"
    )
    assert record.lemma_fields["error_type"] == "RuntimeError"
    assert len(record.lemma_fields["error_stack_hash"]) == 64
    assert "CANARY" not in repr(record.lemma_fields)
