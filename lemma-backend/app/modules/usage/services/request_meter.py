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
        self, request_id: UUID, now: datetime, *, priceable: bool = True
    ) -> bool: ...

    async def record(self, receipt: RequestReceipt) -> bool: ...


class RequestMeter:
    def __init__(self, gateway: RequestAccountingGateway) -> None:
        self.gateway = gateway
        self.pending: dict[UUID, RequestReceipt] = {}
        self.closed = False
        self.require_reconciliation = False

    async def before(self, *, priceable: bool) -> tuple[UUID, datetime, bool]:
        if self.closed:
            raise AccountingConflictError("Metering scope is closed")
        if self.require_reconciliation:
            raise UsageReportingError()
        await self.flush()
        request_id, occurred_at = uuid4(), datetime.now(timezone.utc)
        limited = await self.gateway.begin(request_id, occurred_at, priceable=priceable)
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
