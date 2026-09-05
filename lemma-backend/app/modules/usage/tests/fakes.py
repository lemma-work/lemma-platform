"""An accounting gateway for testing the worker's batching, without a database."""

import asyncio
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory

from app.modules.usage.domain.accounting import (
    AccountingConflictError,
    Allocation,
    UsageBatch,
)
from app.modules.usage.domain.ports import AccountingGateway


class FailOnceUnitOfWorkFactory(UnitOfWorkFactory):
    """Fail a transaction boundary once, then use the real database factory."""

    def __init__(self, wrapped: UnitOfWorkFactory) -> None:
        self.wrapped = wrapped
        self.failed = False

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[SqlAlchemyUnitOfWork]:
        if not self.failed:
            self.failed = True
            raise ConnectionError("receipt status transaction unavailable")
        async with self.wrapped() as uow:
            yield uow


class MemoryAccounting(AccountingGateway):
    def __init__(self, *, amount: Decimal = Decimal("1"), limited: bool = True) -> None:
        self.amount = amount
        self.limited = limited
        self.allocations: list[Allocation] = []
        self.receipts: dict[tuple[UUID, int], UsageBatch] = {}
        self.checkpointed = asyncio.Event()
        self.fail_ack = False
        self.closed_allocations: set[UUID] = set()

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
        elif batch.allocation_id in self.closed_allocations:
            raise AccountingConflictError("Allocation has already closed")
        self.receipts[key] = batch
        if batch.close:
            self.closed_allocations.add(batch.allocation_id)
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
