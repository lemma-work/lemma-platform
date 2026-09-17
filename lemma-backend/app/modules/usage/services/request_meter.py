"""Persist each request immediately and retain failed writes for safe replay."""

from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID, uuid4

from app.modules.usage.domain.accounting import (
    AccountingConflictError,
    RequestReceipt,
)
from app.modules.usage.domain.errors import UsageReportingError


class RequestAccountingGateway(Protocol):
    async def begin(
        self,
        request_id: UUID,
        now: datetime,
        *,
        priceable: bool = True,
        in_flight: bool = False,
    ) -> bool: ...

    async def record(self, receipt: RequestReceipt) -> bool: ...


class RequestMeter:
    def __init__(
        self,
        gateway: RequestAccountingGateway,
        *,
        inside_admitted_run: bool = False,
    ) -> None:
        self.gateway = gateway
        self.pending: dict[UUID, RequestReceipt] = {}
        self.closed = False
        self.require_reconciliation = False
        #: Whether this scope has already put a request to a provider. A run
        #: that has spent tokens must not be killed by a refusal that could
        #: only ever have been made before it started -- see `begin`.
        self.admitted = 0
        #: The same, for spend an *enclosing* execution has already made. A
        #: vision delegate or a sub-agent opens its own scope inside a run that
        #: is already under way; its own counter starts at zero, and without
        #: this its first request would be judged as the first request of a
        #: fresh run and could be refused for work already admitted and billed.
        self.inside_admitted_run = inside_admitted_run

    async def before(self, *, priceable: bool) -> tuple[UUID, datetime, bool]:
        if self.closed:
            raise AccountingConflictError("Metering scope is closed")
        if self.require_reconciliation:
            raise UsageReportingError()
        await self.flush()
        request_id, occurred_at = uuid4(), datetime.now(timezone.utc)
        limited = await self.gateway.begin(
            request_id,
            occurred_at,
            priceable=priceable,
            in_flight=self.inside_admitted_run or self.admitted > 0,
        )
        self.admitted += 1
        return request_id, occurred_at, limited

    async def after(self, receipt: RequestReceipt) -> None:
        self.pending[receipt.request_id] = receipt
        await self.gateway.record(receipt)
        self.pending.pop(receipt.request_id, None)

    async def flush(self) -> None:
        for receipt in list(self.pending.values()):
            await self.after(receipt)

    async def close(self) -> None:
        await self.flush()
        self.closed = True
