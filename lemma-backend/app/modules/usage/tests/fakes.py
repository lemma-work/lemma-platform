"""An accounting gateway for testing the worker's batching, without a database."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from app.modules.usage.domain.accounting import Allocation, UsageBatch
from app.modules.usage.services.batch_meter import AccountingGateway


class MemoryAccounting(AccountingGateway):
    def __init__(self, *, amount: Decimal = Decimal("1"), limited: bool = True) -> None:
        self.amount = amount
        self.limited = limited
        self.allocations: list[Allocation] = []
        self.receipts: dict[tuple[UUID, int], UsageBatch] = {}
        self.checkpointed = asyncio.Event()
        self.fail_ack = False

    async def open(
        self, allocation_id: UUID, required: Decimal | None, now: datetime
    ) -> Allocation:
        allocation = Allocation(
            id=allocation_id,
            amount=self.amount,
            limited=self.limited,
            expires_at=now + timedelta(hours=1),
            window_end=now + timedelta(days=1),
        )
        self.allocations.append(allocation)
        return allocation

    async def checkpoint(self, batch: UsageBatch, now: datetime) -> Allocation:
        key = (batch.allocation_id, batch.sequence)
        if key in self.receipts:
            assert self.receipts[key] == batch
        self.receipts[key] = batch
        self.checkpointed.set()
        if self.fail_ack:
            self.fail_ack = False
            raise ConnectionError("checkpoint committed but acknowledgement was lost")
        spent = sum(
            (receipt.cost or 0) + receipt.uncertain
            for receipt in self.receipts.values()
            if receipt.allocation_id == batch.allocation_id
        )
        return Allocation(
            id=batch.allocation_id,
            amount=self.amount - spent,
            limited=self.limited,
            expires_at=now + timedelta(hours=1),
            window_end=now + timedelta(days=1),
        )
