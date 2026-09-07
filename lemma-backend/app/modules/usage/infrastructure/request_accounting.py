"""Current-budget checks and idempotent per-request usage transactions."""

import hashlib
from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
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
from app.modules.usage.domain.events import ModelUsageEvent, UsageLimitWarningEvent
from app.modules.usage.infrastructure.cost_expressions import recorded_cost
from app.modules.usage.infrastructure.models import UsageLimitCounter, UsageRecord
from app.modules.usage.infrastructure.price_catalog import RateCard


def _key(window: BudgetWindow) -> tuple[str, str, str, datetime]:
    return window.kind, str(window.organization_id), str(window.user_id), window.start


async def lock_counters(
    session: AsyncSession, windows: list[BudgetWindow]
) -> dict[tuple[str, str, str, datetime], UsageLimitCounter]:
    counters: dict[tuple[str, str, str, datetime], UsageLimitCounter] = {}
    for key, window in sorted({_key(w): w for w in windows}.items()):
        await session.scalar(
            insert(UsageLimitCounter)
            .values(
                organization_id=window.organization_id,
                user_id=window.user_id,
                window_kind=window.kind,
                window_start=window.start,
                window_end=window.end,
                used_usd=0,
                reserved_usd=0,
            )
            .on_conflict_do_nothing()
            .returning(UsageLimitCounter.id)
        )
        counter = (
            await session.scalars(
                select(UsageLimitCounter)
                .where(
                    UsageLimitCounter.organization_id == window.organization_id,
                    UsageLimitCounter.user_id == window.user_id,
                    UsageLimitCounter.window_kind == window.kind,
                    UsageLimitCounter.window_start == window.start,
                )
                .with_for_update()
            )
        ).one()
        # Legacy writers and changing exclusion policies make a cached total unsafe.
        query = select(func.coalesce(func.sum(recorded_cost()), 0)).where(
            UsageRecord.profile_scope == "SYSTEM",
            UsageRecord.occurred_at >= window.start,
            UsageRecord.occurred_at < window.end,
        )
        if window.organization_id is not None:
            query = query.where(UsageRecord.organization_id == window.organization_id)
        if window.user_id is not None:
            query = query.where(UsageRecord.user_id == window.user_id)
        if window.excluded_organization_ids:
            query = query.where(
                or_(
                    UsageRecord.organization_id.is_(None),
                    UsageRecord.organization_id.notin_(
                        window.excluded_organization_ids
                    ),
                )
            )
        counter.used_usd = money(await session.scalar(query) or 0)
        if counter.limit_usd != window.limit:
            counter.warning_emitted = False
            counter.limit_usd = window.limit
        counters[key] = counter
    return counters


def exhausted(
    counters: dict[tuple[str, str, str, datetime], UsageLimitCounter],
    windows: list[BudgetWindow],
) -> bool:
    return any(
        w.limit is not None and counters[_key(w)].used_usd >= w.limit for w in windows
    )


async def check(session: AsyncSession, windows: list[BudgetWindow]) -> None:
    if exhausted(await lock_counters(session, windows), windows):
        raise UsageLimitExceededError("Current usage allowance is exhausted")


async def begin(
    session: AsyncSession,
    request_id: UUID,
    identity: MeteringIdentity,
    pricing: RateCard,
    windows: list[BudgetWindow],
    now: datetime,
) -> None:
    row = await session.scalar(
        select(UsageRecord)
        .where(UsageRecord.request_id == request_id)
        .with_for_update()
    )
    if row is not None:
        raise AccountingConflictError(
            "Provider request identifier has already been used"
        )
    session.add(
        UsageRecord(
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
                "execution_id": str(identity.execution_id),
                "pricing": pricing.model_dump(mode="json"),
                "request_count": 1,
                "usage": {"request_count": 1, "unconfirmed_requests": 1},
            },
        )
    )
    await session.flush()
    await check(session, windows)


async def record(
    session: AsyncSession,
    receipt: RequestReceipt,
    identity: MeteringIdentity,
    historical_windows: list[BudgetWindow],
    current_windows: list[BudgetWindow],
    events: list[DomainEvent],
    warning_fraction: Decimal,
) -> bool:
    row = (
        await session.scalars(
            select(UsageRecord)
            .where(UsageRecord.request_id == receipt.request_id)
            .with_for_update()
        )
    ).one()
    metadata = dict(row.record_metadata or {})
    if metadata.get("execution_id") != str(identity.execution_id) or not _same_identity(
        row, identity
    ):
        raise AccountingConflictError("Request receipt belongs to another execution")
    if row.occurred_at != receipt.occurred_at:
        raise AccountingConflictError("Request receipt timestamp cannot change")
    digest = hashlib.sha256(receipt.model_dump_json().encode()).hexdigest()
    previous = metadata.get("receipt_digest")
    if previous is not None and previous != digest:
        raise AccountingConflictError("A request cannot describe different usage")
    counters = await lock_counters(session, historical_windows + current_windows)
    if previous is not None:
        return exhausted(counters, current_windows)
    cost = money(receipt.cost) if receipt.cost is not None else None
    row.input_tokens, row.output_tokens = (
        receipt.counts.input_tokens,
        receipt.counts.output_tokens,
    )
    row.cached_input_tokens = receipt.counts.cache_read_tokens
    row.cache_write_tokens = receipt.counts.cache_write_tokens
    row.cost_amount = cost
    row.cost_usd = float(cost) if cost is not None else None
    row.record_metadata = {
        **metadata,
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
    if cost is not None:
        for window in historical_windows:
            counter = counters[_key(window)]
            counter.used_usd += cost
            _warning(counter, events, warning_fraction)
    await session.flush()
    events.append(
        ModelUsageEvent(
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
    )
    return exhausted(counters, current_windows)


def _warning(
    counter: UsageLimitCounter, events: list[DomainEvent], fraction: Decimal
) -> None:
    if (
        counter.warning_emitted
        or counter.limit_usd is None
        or counter.limit_usd <= 0
        or counter.used_usd < counter.limit_usd * fraction
    ):
        return
    counter.warning_emitted = True
    events.append(
        UsageLimitWarningEvent(
            counter_id=counter.id,
            organization_id=counter.organization_id,
            user_id=counter.user_id,
            window_kind=counter.window_kind,
            used_usd=float(counter.used_usd),
            limit_usd=float(counter.limit_usd),
        )
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
