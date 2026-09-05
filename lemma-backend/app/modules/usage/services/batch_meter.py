"""Price requests locally and checkpoint exclusive authority in batches."""

import asyncio
from collections.abc import Callable
from datetime import datetime, timezone
from decimal import Decimal
from typing import Protocol
from uuid import UUID, uuid4

from app.core.request_context import create_inherited_task
from app.modules.usage.domain.accounting import (
    Allocation,
    TokenCounts,
    UsageBatch,
    AccountingConflictError,
)


class AccountingGateway(Protocol):
    async def open(
        self, allocation_id: UUID, required: Decimal | None, now: datetime
    ) -> Allocation: ...
    async def checkpoint(self, batch: UsageBatch, now: datetime) -> Allocation: ...


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class BatchMeter:
    def __init__(
        self,
        gateway: AccountingGateway,
        *,
        request_interval: int = 10,
        seconds: float = 30,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.gateway = gateway
        self.request_interval = request_interval
        self.seconds = seconds
        self.clock = clock or utc_now
        self.lock = asyncio.Lock()
        self.allocation: Allocation | None = None
        self.open_id = uuid4()
        self.sequence = 0
        self.counts = TokenCounts()
        self.cost: Decimal | None = Decimal(0)
        self.uncertain = Decimal(0)
        self.inflight: dict[UUID, Decimal] = {}
        self.first_request_at: datetime | None = None
        self.pending: UsageBatch | None = None
        self.timer: asyncio.Task[None] | None = None
        self.closed = False

    async def before(self, bound: Decimal | None) -> UUID:
        async with self.lock:
            if self.closed:
                raise AccountingConflictError("Metering scope is closed")
            if self.timer is not None and self.timer.done():
                self.timer.result()
            now = self.clock()
            if self.pending is not None:
                await self._flush(now)
            if self.allocation is not None and (
                now >= self.allocation.window_end or now >= self.allocation.expires_at
            ):
                if self.inflight:
                    raise AccountingConflictError(
                        "An expired allocation still has requests in flight"
                    )
                await self._flush(now, close=True)
                self._reset_allocation()
            if self.allocation is None:
                self.allocation = await self.gateway.open(self.open_id, bound, now)
            await self._authorize_request(bound, now)
            ticket = uuid4()
            self.inflight[ticket] = bound or Decimal(0)
            self.first_request_at = self.first_request_at or now
            if self.timer is None:
                self.timer = create_inherited_task(self._periodic_flush())
            return ticket

    async def _authorize_request(self, bound: Decimal | None, now: datetime) -> None:
        assert self.allocation is not None

        if self.allocation.limited:
            required = bound if bound is not None else self.allocation.amount + 1
            pending_spend = (
                ((self.pending.cost or 0) + self.pending.uncertain)
                if self.pending
                else Decimal(0)
            )
            available = (
                self.allocation.amount
                - (self.cost or 0)
                - self.uncertain
                - pending_spend
                - sum(self.inflight.values())
            )
            if required > available:
                if self.inflight:
                    raise AccountingConflictError(
                        "Concurrent requests have allocated the remaining budget"
                    )
                await self._flush(now, close=True)
                self._reset_allocation()
                self.allocation = await self.gateway.open(self.open_id, bound, now)
            if bound is None or bound > self.allocation.amount:
                raise AccountingConflictError("Request has no authorized cost bound")

    async def after(
        self, ticket: UUID, counts: TokenCounts | None, cost: Decimal | None
    ) -> None:
        async with self.lock:
            bound = self.inflight.pop(ticket)
            if counts is None:
                self.uncertain += bound
                self.counts = self.counts.plus(
                    TokenCounts(request_count=1, unconfirmed_requests=1)
                )
                if not bound and not self.cost:
                    self.cost = None
            else:
                if (
                    self.allocation is not None
                    and self.allocation.limited
                    and (cost is None or cost > bound)
                ):
                    self.uncertain += bound
                    raise AccountingConflictError(
                        "Provider usage exceeded its authorized bound"
                    )
                self._add_usage(counts, cost)
            if self.counts.request_count >= self.request_interval:
                await self._flush(self.clock())

    def _add_usage(self, counts: TokenCounts, cost: Decimal | None) -> None:
        if cost is None:
            counts = counts.model_copy(
                update={"unpriced_requests": counts.request_count}
            )
            if not self.cost:
                self.cost = None
        else:
            self.cost = (self.cost or Decimal(0)) + cost
        self.counts = self.counts.plus(counts)

    async def flush(self) -> None:
        async with self.lock:
            if (
                self.inflight
                or self.counts.request_count
                or self.uncertain
                or self.pending
            ):
                await self._flush(self.clock())

    async def close(self) -> None:
        if self.timer is not None:
            self.timer.cancel()
            await asyncio.gather(self.timer, return_exceptions=True)
        async with self.lock:
            if self.closed:
                return
            self.uncertain += sum(self.inflight.values())
            self.inflight.clear()
            if self.pending is not None:
                await self._flush(self.clock())
            await self._flush(self.clock(), close=True)
            self.closed = True

    async def _periodic_flush(self) -> None:
        while True:
            await asyncio.sleep(self.seconds)
            await self.flush()

    async def _flush(self, now: datetime, *, close: bool = False) -> None:
        if self.allocation is None:
            return
        if self.pending is None:
            self.pending = UsageBatch(
                allocation_id=self.allocation.id,
                sequence=self.sequence + 1,
                counts=self.counts,
                cost=self.cost,
                uncertain=self.uncertain,
                occurred_at=self.first_request_at or now,
                close=close,
            )
            self.counts = TokenCounts()
            self.cost = Decimal(0)
            self.uncertain = Decimal(0)
            self.first_request_at = now if self.inflight else None
        batch = self.pending
        self.allocation = await self.gateway.checkpoint(batch, now)
        self.sequence = batch.sequence
        self.pending = None

    def _reset_allocation(self) -> None:
        self.allocation = None
        self.open_id = uuid4()
        self.sequence = 0
