"""Meter one paid request that is not a pydantic-ai model call.

`MeteredModel` meters model requests by wrapping the model. A provider reached
over plain HTTP -- a classifier priced per token, say -- has no model to wrap,
and without this its spend went nowhere: not into the ledger, not against any
limit. This is the same admit, call, record sequence `MeteredModel._dispatch`
runs, with the caller telling it what the provider reported.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from decimal import Decimal
from uuid import UUID

import anyio

from app.modules.usage.domain.accounting import RequestReceipt, TokenCounts
from app.modules.usage.domain.errors import UsageContextMissingError
from app.modules.usage.infrastructure.price_catalog import RateCard
from app.modules.usage.services.metering_scope import current_metering_scope


class ExternalRequestOutcome:
    """What the caller learned from the provider, set before the request closes."""

    def __init__(self) -> None:
        self.input_tokens: int | None = None
        self.output_tokens = 0
        self.rejected = False

    def settle(self, *, input_tokens: int, output_tokens: int = 0) -> None:
        """The provider answered and reported this usage."""
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens

    def reject(self) -> None:
        """The provider refused the request outright, so it cost nothing."""
        self.rejected = True

    def receipt(
        self, request_id: UUID, occurred_at: datetime, card: RateCard
    ) -> RequestReceipt:
        if self.rejected:
            return RequestReceipt(
                request_id=request_id,
                occurred_at=occurred_at,
                counts=TokenCounts(request_count=1),
                cost=Decimal(0),
            )
        if self.input_tokens is None:
            # Sent, and nothing came back that says what it cost.
            return RequestReceipt(
                request_id=request_id,
                occurred_at=occurred_at,
                counts=TokenCounts(request_count=1, unconfirmed_requests=1),
            )
        counts = TokenCounts(
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            request_count=1,
        )
        cost = card.price(counts)
        if cost is None:
            counts = counts.model_copy(update={"unpriced_requests": 1})
        return RequestReceipt(
            request_id=request_id, occurred_at=occurred_at, counts=counts, cost=cost
        )


@asynccontextmanager
async def metered_external_request(
    profile: Mapping[str, object], *, source: str | None = None
) -> AsyncIterator[ExternalRequestOutcome]:
    """Admit one request against the current execution's limits, then record it.

    Raises `UsageLimitExceededError` before anything is sent when a limit
    refuses it, and `UsageContextMissingError` outside `metering_execution`.
    """
    scope = current_metering_scope()
    if scope is None:
        raise UsageContextMissingError()
    meter, card = scope.meter(profile, source)
    request_id, occurred_at, limited = await meter.before(priceable=True)
    outcome = ExternalRequestOutcome()
    try:
        yield outcome
    finally:
        with anyio.fail_after(10, shield=True):
            receipt = outcome.receipt(request_id, occurred_at, card)
            await meter.after(receipt)
            if limited and outcome.input_tokens is not None and receipt.cost is None:
                meter.require_reconciliation = True
