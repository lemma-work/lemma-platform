import asyncio
from decimal import Decimal

import pytest

from app.modules.usage.domain.accounting import TokenCounts
from app.modules.usage.services.batch_meter import BatchMeter
from app.modules.usage.tests.fakes import MemoryAccounting


async def test_requests_within_an_allocation_only_write_at_batch_boundaries() -> None:
    gateway = MemoryAccounting()
    meter = BatchMeter(gateway, request_interval=10, seconds=3600)
    try:
        for _ in range(25):
            ticket = await meter.before(Decimal("0.02"))
            await meter.after(
                ticket, TokenCounts(input_tokens=100, request_count=1), Decimal("0.01")
            )
        assert len(gateway.allocations) == 1
        assert len(gateway.receipts) == 2
    finally:
        await meter.close()
    assert len(gateway.receipts) == 3
    assert (
        sum(receipt.counts.input_tokens for receipt in gateway.receipts.values())
        == 2500
    )
    assert sum(receipt.cost or 0 for receipt in gateway.receipts.values()) == Decimal(
        "0.25"
    )
    assert meter.timer is not None and meter.timer.done()


async def test_lost_checkpoint_acknowledgement_retries_the_identical_receipt() -> None:
    gateway = MemoryAccounting()
    meter = BatchMeter(gateway, request_interval=1, seconds=3600)
    try:
        ticket = await meter.before(Decimal("0.02"))
        gateway.fail_ack = True
        with pytest.raises(ConnectionError):
            await meter.after(
                ticket, TokenCounts(input_tokens=100, request_count=1), Decimal("0.01")
            )
        await meter.flush()
        assert len(gateway.receipts) == 1
        assert meter.pending is None
    finally:
        await meter.close()
    assert (
        sum(receipt.counts.input_tokens for receipt in gateway.receipts.values()) == 100
    )


async def test_timer_checkpoints_before_a_long_run_finishes() -> None:
    gateway = MemoryAccounting()
    meter = BatchMeter(gateway, request_interval=10, seconds=0.01)
    try:
        ticket = await meter.before(Decimal("0.02"))
        await meter.after(
            ticket, TokenCounts(input_tokens=100, request_count=1), Decimal("0.01")
        )
        async with asyncio.timeout(2):
            await gateway.checkpointed.wait()
        assert not meter.closed
        assert (
            sum(receipt.counts.input_tokens for receipt in gateway.receipts.values())
            == 100
        )
    finally:
        await meter.close()


async def test_ambiguous_request_retains_its_bound_separate_from_confirmed_cost() -> (
    None
):
    gateway = MemoryAccounting()
    meter = BatchMeter(gateway, seconds=3600)
    try:
        ticket = await meter.before(Decimal("0.20"))
        await meter.after(ticket, None, None)
    finally:
        await meter.close()
    receipt = next(iter(gateway.receipts.values()))
    assert receipt.cost == 0
    assert receipt.uncertain == Decimal("0.20")
    assert receipt.counts.request_count == 1
    assert receipt.counts.unconfirmed_requests == 1


async def test_request_too_large_for_local_remainder_renews_before_dispatch() -> None:
    gateway = MemoryAccounting()
    meter = BatchMeter(gateway, seconds=3600)
    try:
        ticket = await meter.before(Decimal("0.90"))
        await meter.after(ticket, TokenCounts(request_count=1), Decimal("0.80"))
        next_ticket = await meter.before(Decimal("0.40"))
        assert len(gateway.allocations) == 2
        assert next(iter(gateway.receipts.values())).close
        await meter.after(next_ticket, TokenCounts(request_count=1), Decimal("0.10"))
    finally:
        await meter.close()


async def test_unpriced_request_does_not_erase_confirmed_cost_in_the_batch() -> None:
    gateway = MemoryAccounting(limited=False)
    meter = BatchMeter(gateway, seconds=3600)
    try:
        unknown = await meter.before(None)
        await meter.after(unknown, TokenCounts(input_tokens=100, request_count=1), None)
        known = await meter.before(Decimal(".2"))
        await meter.after(
            known, TokenCounts(input_tokens=100, request_count=1), Decimal(".1")
        )
    finally:
        await meter.close()
    receipt = next(iter(gateway.receipts.values()))
    assert receipt.cost == Decimal(".1")
    assert receipt.counts.unpriced_requests == 1
    assert receipt.counts.request_count == 2


async def test_failed_unpriced_request_still_leaves_an_attempt_receipt() -> None:
    gateway = MemoryAccounting(limited=False)
    meter = BatchMeter(gateway, seconds=3600)
    try:
        ticket = await meter.before(None)
        await meter.after(ticket, None, None)
    finally:
        await meter.close()
    receipt = next(iter(gateway.receipts.values()))
    assert receipt.cost is None
    assert receipt.counts.unconfirmed_requests == 1
    assert receipt.counts.request_count == 1
