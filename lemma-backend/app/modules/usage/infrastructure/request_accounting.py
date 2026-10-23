"""Current-budget checks and idempotent per-request usage transactions.

A request is admitted against each limit window's running total and settled by
adding its cost to it; ``spend_counters`` is how that total stays the ledger's.
"""

import hashlib
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.domain.events import DomainEvent
from app.modules.usage.domain.accounting import (
    AccountingConflictError,
    BudgetWindow,
    MeteringIdentity,
    RequestReceipt,
    money,
)
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.domain.events import ModelUsageEvent
from app.modules.usage.infrastructure import spend_counters
from app.modules.usage.infrastructure.models import UsageRecord
from app.modules.usage.infrastructure.price_catalog import RateCard


async def limit_reached(session: AsyncSession, windows: list[BudgetWindow]) -> bool:
    """Whether any of ``windows`` has spent its limit, so the next request is refused."""
    found = await spend_counters.counters(session, windows)
    return spend_counters.exhausted(found, windows)


async def check(session: AsyncSession, windows: list[BudgetWindow]) -> None:
    if await limit_reached(session, windows):
        raise UsageLimitExceededError("Current usage allowance is exhausted")


async def journal(
    session: AsyncSession,
    request_id: UUID,
    identity: MeteringIdentity,
    pricing: RateCard,
    now: datetime,
) -> None:
    """The ledger row a request writes before it is sent: pending, unpriced."""
    inserted = await session.scalar(
        insert(UsageRecord)
        .values(
            request_id=request_id,
            organization_id=identity.organization_id,
            user_id=identity.user_id,
            pod_id=identity.pod_id,
            agent_id=identity.agent_id,
            agent_run_id=identity.agent_run_id,
            parent_agent_run_id=identity.parent_agent_run_id,
            conversation_id=identity.conversation_id,
            source_type=identity.source_type,
            source_id=identity.source_id,
            profile_id=identity.profile_id,
            profile_scope=identity.profile_scope,
            model_name=identity.model_name,
            occurred_at=now,
            record_metadata={
                "metering_state": "PENDING",
                # No cost until the receipt lands, and none ever if it is
                # lost: said, so the row never reads as a request that was free.
                "pricing_missing": True,
                "execution_id": str(identity.execution_id),
                "pricing": pricing.model_dump(mode="json"),
                "request_count": 1,
                "usage": {"request_count": 1, "unconfirmed_requests": 1},
            },
        )
        .on_conflict_do_nothing(
            index_elements=[UsageRecord.request_id],
            index_where=UsageRecord.request_id.is_not(None),
        )
        .returning(UsageRecord.id)
    )
    if inserted is None:
        raise AccountingConflictError(
            "Provider request identifier has already been used"
        )


async def begin(
    session: AsyncSession,
    request_id: UUID,
    identity: MeteringIdentity,
    pricing: RateCard,
    windows: list[BudgetWindow],
    now: datetime,
) -> None:
    await check(session, windows)
    await journal(session, request_id, identity, pricing, now)


async def record(
    session: AsyncSession,
    receipt: RequestReceipt,
    identity: MeteringIdentity,
    historical_windows: list[BudgetWindow],
    current_windows: list[BudgetWindow],
    events: list[DomainEvent],
    warning_fraction: Decimal,
) -> bool:
    row = await _journaled(session, receipt, identity)
    digest = hashlib.sha256(receipt.model_dump_json().encode()).hexdigest()
    previous = (row.record_metadata or {}).get("receipt_digest")
    if previous is not None and previous != digest:
        raise AccountingConflictError("A request cannot describe different usage")
    if previous is not None:
        found = await spend_counters.read(session, current_windows)
        return spend_counters.exhausted(found, current_windows)
    cost = money(receipt.cost) if receipt.cost is not None else None
    _settle(row, receipt, digest, cost)
    await session.flush()
    charged: dict[spend_counters.CounterKey, spend_counters.Counter] = {}
    if cost is not None:
        charged = await spend_counters.charge(session, historical_windows, cost)
        for window in spend_counters.ordered(historical_windows):
            if (counter := charged.get(spend_counters.key(window))) is not None:
                charged[spend_counters.key(window)] = await spend_counters.warn_once(
                    session, counter, window, events, warning_fraction
                )
    unread = [w for w in current_windows if spend_counters.key(w) not in charged]
    found = {**charged, **await spend_counters.read(session, unread)}
    events.append(_usage_event(row))
    return spend_counters.exhausted(found, current_windows)


async def _journaled(
    session: AsyncSession, receipt: RequestReceipt, identity: MeteringIdentity
) -> UsageRecord:
    """The receipt's own journal entry, locked, once it is shown to be its own."""
    row = (
        await session.scalars(
            select(UsageRecord)
            .where(UsageRecord.request_id == receipt.request_id)
            .with_for_update()
        )
    ).one()
    metadata = row.record_metadata or {}
    if metadata.get("execution_id") != str(identity.execution_id) or not _same_identity(
        row, identity
    ):
        raise AccountingConflictError("Request receipt belongs to another execution")
    if row.occurred_at != receipt.occurred_at:
        raise AccountingConflictError("Request receipt timestamp cannot change")
    return row


def _settle(
    row: UsageRecord, receipt: RequestReceipt, digest: str, cost: Decimal | None
) -> None:
    row.input_tokens, row.output_tokens = (
        receipt.counts.input_tokens,
        receipt.counts.output_tokens,
    )
    row.cached_input_tokens = receipt.counts.cache_read_tokens
    row.cache_write_tokens = receipt.counts.cache_write_tokens
    row.cost_amount = cost
    row.cost_usd = float(cost) if cost is not None else None
    row.record_metadata = {
        **(row.record_metadata or {}),
        "receipt_digest": digest,
        "usage": receipt.counts.model_dump(mode="json"),
        "request_count": receipt.counts.request_count,
        "pricing_missing": cost is None,
        "metering_state": "UNCONFIRMED"
        if receipt.counts.unconfirmed_requests
        else "UNPRICED"
        if cost is None
        else "RECORDED",
    }


def _usage_event(row: UsageRecord) -> ModelUsageEvent:
    return ModelUsageEvent(
        usage_id=row.id,
        organization_id=row.organization_id,
        pod_id=row.pod_id,
        user_id=row.user_id,
        agent_id=row.agent_id,
        conversation_id=row.conversation_id,
        agent_run_id=row.agent_run_id,
        parent_agent_run_id=row.parent_agent_run_id,
        source_type=row.source_type,
        source_id=row.source_id,
        profile_id=row.profile_id,
        profile_scope=row.profile_scope,
        model_name=row.model_name,
        input_tokens=row.input_tokens,
        output_tokens=row.output_tokens,
        cost_usd=row.cost_usd,
        metadata=row.record_metadata,
    )


def _same_identity(row: UsageRecord, identity: MeteringIdentity) -> bool:
    return (
        row.user_id,
        row.organization_id,
        row.profile_id,
        row.profile_scope,
        row.model_name,
        row.pod_id,
        row.agent_id,
        row.agent_run_id,
        row.parent_agent_run_id,
        row.conversation_id,
        row.source_type,
        row.source_id,
    ) == (
        identity.user_id,
        identity.organization_id,
        identity.profile_id,
        identity.profile_scope,
        identity.model_name,
        identity.pod_id,
        identity.agent_id,
        identity.agent_run_id,
        identity.parent_agent_run_id,
        identity.conversation_id,
        identity.source_type,
        identity.source_id,
    )
