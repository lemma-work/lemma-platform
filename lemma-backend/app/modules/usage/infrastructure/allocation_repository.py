"""Allocate and settle money in short, idempotent PostgreSQL transactions."""

import hashlib
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, or_, select
from app.core.domain.events import DomainEvent
from app.modules.usage.domain.events import ModelUsageEvent, UsageLimitWarningEvent
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.domain.accounting import (
    AccountingConflictError,
    Allocation,
    AllocationState,
    BudgetWindow,
    MeteringIdentity,
    UsageBatch,
    money,
)
from app.modules.usage.domain.errors import UsageLimitExceededError
from app.modules.usage.infrastructure.allocation_models import UsageAllocation
from app.modules.usage.infrastructure.cost_expressions import recorded_cost
from app.modules.usage.infrastructure.models import UsageLimitCounter, UsageRecord
from app.modules.usage.infrastructure.price_catalog import RateCard


def _allocation(row: UsageAllocation) -> Allocation:
    return Allocation(
        id=row.id,
        amount=row.remaining - row.uncertain,
        limited=row.limited,
        expires_at=row.expires_at,
        window_end=row.window_end,
    )


async def _window_counter(
    session: AsyncSession, window: BudgetWindow
) -> UsageLimitCounter:
    inserted = await session.scalar(
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
    if inserted is not None:
        # Only a newly created window scans legacy history. Subsequent admissions
        # read the transactionally maintained counter, never the usage ledger.
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
                    UsageRecord.organization_id.not_in(
                        window.excluded_organization_ids
                    ),
                )
            )
        counter.used_usd = money(await session.scalar(query) or 0)
    counter.limit_usd = window.limit
    return counter


async def open_allocation(
    session: AsyncSession,
    *,
    allocation_id: UUID,
    identity: MeteringIdentity,
    pricing: RateCard,
    windows: list[BudgetWindow],
    required: Decimal,
    target: Decimal,
    now: datetime,
    timeout_seconds: int,
) -> Allocation:
    existing = await session.get(UsageAllocation, allocation_id)
    if existing is not None:
        if existing.identity != identity.model_dump(
            mode="json"
        ) or existing.pricing != pricing.model_dump(mode="json"):
            raise AccountingConflictError("Allocation identity cannot change")
        return _allocation(existing)
    ordered = sorted(
        windows, key=lambda w: (w.kind, str(w.organization_id), str(w.user_id), w.start)
    )
    counters = [await _window_counter(session, window) for window in ordered]
    remaining = [
        window.limit - counter.used_usd - counter.reserved_usd
        for window, counter in zip(ordered, counters, strict=True)
        if window.limit is not None
    ]
    limited = bool(remaining)
    amount = _allocation_target(required, target) if limited else Decimal(0)
    if limited:
        amount = min(amount, min(remaining))
        if amount < required or amount < 0:
            binding = next(
                w
                for w, c in zip(ordered, counters, strict=True)
                if w.limit is not None
                and w.limit - c.used_usd - c.reserved_usd < required
            )
            raise UsageLimitExceededError(
                f"The {binding.kind} usage allowance cannot cover this request. It resets at {binding.end.isoformat()}."
            )
    for counter in counters:
        counter.reserved_usd += amount
    end = min((window.end for window in windows), default=now + timedelta(days=1))
    row = UsageAllocation(
        id=allocation_id,
        identity=identity.model_dump(mode="json"),
        pricing=pricing.model_dump(mode="json"),
        counter_ids=[c.id for c in counters],
        allocated=amount,
        remaining=amount,
        uncertain=Decimal(0),
        limited=limited,
        sequence=0,
        state=AllocationState.ACTIVE,
        expires_at=now + timedelta(seconds=timeout_seconds),
        window_end=end,
    )
    session.add(row)
    await session.flush()
    return _allocation(row)


async def checkpoint(
    session: AsyncSession,
    batch: UsageBatch,
    *,
    now: datetime,
    timeout_seconds: int,
    events: list[DomainEvent] | None = None,
    warning_fraction: Decimal = Decimal("0.8"),
) -> Allocation:
    row = (
        await session.scalars(
            select(UsageAllocation)
            .where(UsageAllocation.id == batch.allocation_id)
            .with_for_update()
        )
    ).one()
    digest = hashlib.sha256(batch.model_dump_json().encode()).hexdigest()
    if await _already_settled(session, row, batch, digest):
        return _allocation(row)
    if row.state == AllocationState.CLOSED or batch.sequence != row.sequence + 1:
        raise AccountingConflictError(
            "Checkpoint sequence is not the next unsettled batch"
        )
    cost = money(batch.cost or 0)
    counters = list(
        (
            await session.scalars(
                select(UsageLimitCounter)
                .where(UsageLimitCounter.id.in_(row.counter_ids))
                .order_by(
                    UsageLimitCounter.window_kind,
                    UsageLimitCounter.organization_id,
                    UsageLimitCounter.user_id,
                    UsageLimitCounter.window_start,
                )
                .with_for_update()
            )
        ).all()
    )
    release, liability = _settle_authority(row, batch, cost)
    for counter in counters:
        if counter.reserved_usd < release:
            raise AccountingConflictError(
                "Reserved counter does not cover its allocation"
            )
        counter.reserved_usd += liability - release
        counter.used_usd += cost
        _collect_warning(counter, events, warning_fraction)
    row.sequence = batch.sequence
    row.last_receipt_digest = digest
    row.state = (
        (AllocationState.UNCERTAIN if row.uncertain else AllocationState.CLOSED)
        if batch.close
        else AllocationState.ACTIVE
    )
    row.expires_at = now + timedelta(seconds=timeout_seconds)
    await _record_batch(session, row, batch, digest, events)
    return _allocation(row)


def _settle_authority(
    row: UsageAllocation, batch: UsageBatch, cost: Decimal
) -> tuple[Decimal, Decimal]:
    # Admission bounds dispatch; a final provider receipt is still a real charge
    # when the bound was wrong. Consume the available hold and account all cost.
    release = min(cost, row.remaining - row.uncertain) if row.limited else Decimal(0)
    row.remaining -= release
    row.uncertain += batch.uncertain
    # Concurrent requests may become uncertain after an overage spent their hold.
    # Preserve that liability without creating any additional spending authority.
    liability = (
        max(Decimal(0), row.uncertain - row.remaining) if row.limited else Decimal(0)
    )
    row.remaining += liability
    if batch.close and row.limited:
        release += row.remaining - row.uncertain
        row.remaining = row.uncertain
    return release, liability


async def _record_batch(
    session: AsyncSession,
    row: UsageAllocation,
    batch: UsageBatch,
    digest: str,
    events: list[DomainEvent] | None,
) -> None:
    if batch.counts.request_count == 0 and not batch.cost and not batch.uncertain:
        await session.flush()
        return
    identity = MeteringIdentity.model_validate(row.identity)
    pricing = RateCard.model_validate(row.pricing)
    record = UsageRecord(
        allocation_id=row.id,
        batch_sequence=batch.sequence,
        receipt_digest=digest,
        organization_id=identity.organization_id,
        pod_id=identity.pod_id,
        user_id=identity.user_id,
        agent_id=identity.agent_id,
        conversation_id=identity.conversation_id,
        agent_run_id=identity.agent_run_id,
        parent_agent_run_id=identity.parent_agent_run_id,
        source_type=identity.source_type,
        source_id=identity.source_id,
        profile_id=identity.profile_id,
        profile_scope=identity.profile_scope,
        model_name=identity.model_name,
        usage_kind="LLM",
        input_tokens=batch.counts.input_tokens,
        output_tokens=batch.counts.output_tokens,
        cached_input_tokens=batch.counts.cache_read_tokens,
        cache_write_tokens=batch.counts.cache_write_tokens,
        cost_amount=batch.cost,
        cost_usd=float(batch.cost) if batch.cost is not None else None,
        cost_source=pricing.source.value,
        occurred_at=batch.occurred_at,
        record_metadata={
            "pricing": row.pricing,
            "pricing_missing": batch.cost is None or batch.counts.unpriced_requests > 0,
            "request_count": batch.counts.request_count,
            "usage": batch.counts.model_dump(mode="json"),
            "uncertain_usd": str(batch.uncertain),
            "over_bound_cost_usd": str(batch.over_bound_cost),
            "execution_id": str(identity.execution_id),
        },
    )
    session.add(record)
    await session.flush()
    if events is not None:
        entity = record.to_entity()
        events.append(
            ModelUsageEvent(
                usage_id=entity.id,
                organization_id=entity.organization_id,
                pod_id=entity.pod_id,
                user_id=entity.user_id,
                agent_id=entity.agent_id,
                conversation_id=entity.conversation_id,
                agent_run_id=entity.agent_run_id,
                parent_agent_run_id=entity.parent_agent_run_id,
                source_type=entity.source_type,
                source_id=entity.source_id,
                profile_id=entity.profile_id,
                profile_scope=entity.profile_scope,
                model_name=entity.model_name,
                usage_kind=entity.usage_kind,
                input_tokens=entity.input_tokens,
                output_tokens=entity.output_tokens,
                units=entity.units,
                cost_usd=entity.cost_usd,
                status=entity.status,
                metadata=entity.metadata,
            )
        )


async def mark_expired_uncertain(
    session: AsyncSession, now: datetime, *, limit: int = 100
) -> int:
    rows = list(
        (
            await session.scalars(
                select(UsageAllocation)
                .where(
                    UsageAllocation.state == AllocationState.ACTIVE,
                    UsageAllocation.expires_at <= now,
                )
                .order_by(UsageAllocation.expires_at)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
        ).all()
    )
    for row in rows:
        # Keep remaining authority reserved. A late receipt may still settle it;
        # recovery must not fabricate either a provider charge or a refund.
        row.state = AllocationState.UNCERTAIN
    await session.flush()
    return len(rows)


def _collect_warning(
    counter: UsageLimitCounter, events: list[DomainEvent] | None, fraction: Decimal
) -> None:
    if events is None or counter.warning_emitted or counter.limit_usd is None:
        return
    if counter.limit_usd <= 0 or counter.used_usd < counter.limit_usd * fraction:
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


async def _already_settled(
    session: AsyncSession, row: UsageAllocation, batch: UsageBatch, digest: str
) -> bool:
    if row.sequence == batch.sequence and row.last_receipt_digest == digest:
        return True
    receipt = await session.scalar(
        select(UsageRecord).where(
            UsageRecord.allocation_id == batch.allocation_id,
            UsageRecord.batch_sequence == batch.sequence,
        )
    )
    if receipt is not None:
        if receipt.receipt_digest != digest:
            raise AccountingConflictError(
                "A checkpoint sequence cannot describe different usage"
            )
        return True
    return False


def _allocation_target(required: Decimal, target: Decimal) -> Decimal:
    return target if required <= target else required + target
