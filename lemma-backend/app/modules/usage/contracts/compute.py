"""Machine time in the usage ledger, and how much of an allowance is left.

The workspace module meters machines in slices and writes each one here as a
``COMPUTE`` record: ``units`` in compute units, ``cost_usd`` at the plan's
price per unit, so machine time counts in the same USD windows as model spend
and, when a plan has one, in a compute allowance of its own.

A slice is written once whoever writes it: its request id is derived from the
machine session and the slice's start, and the ledger's unique index on
request ids turns a second write into nothing.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import NAMESPACE_URL, UUID, uuid5

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.usage.domain.accounting import MACHINE_COMPUTE, POD_MACHINE_COMPUTE
from app.modules.usage.domain.entities import UsageKind
from app.modules.usage.domain.ports import UsageLimitValues, normalize_limit_values
from app.modules.usage.infrastructure.models import UsageRecord

_SLICE_NAMESPACE = uuid5(NAMESPACE_URL, "https://lemma.work/usage/compute-slice")
COMPUTE_SOURCES = (MACHINE_COMPUTE, POD_MACHINE_COMPUTE)


@dataclass(frozen=True, slots=True)
class ComputeSlice:
    session_id: UUID
    machine_id: UUID
    slice_start: datetime
    slice_end: datetime
    compute_units: float
    #: The person billed, or -- for a pod machine -- whoever created it; the
    #: ledger needs a user, and a pod machine's slices never count in theirs.
    user_id: UUID
    organization_id: UUID | None
    pod_id: UUID | None
    billed_to_org: bool
    size: str


async def record_compute_slice(
    uow: SqlAlchemyUnitOfWork, entry: ComputeSlice, *, unit_usd: float | None
) -> bool:
    """Write one slice. False when it was already written.

    Added through the unit of work rather than as a bare insert: a priced
    SYSTEM row added that way marks every spend window it falls into for
    recounting (``models._uncount_windows_of``), so machine time reaches the
    running totals the next admission reads. In a savepoint, because the
    request id is what makes a second write of the slice nothing.
    """
    request_id = uuid5(
        _SLICE_NAMESPACE, f"{entry.session_id}:{entry.slice_start.isoformat()}"
    )
    existing = await uow.session.scalar(
        select(UsageRecord.id).where(UsageRecord.request_id == request_id)
    )
    if existing is not None:
        return False
    cost = None if unit_usd is None else round(entry.compute_units * unit_usd, 9)
    row = UsageRecord(
        organization_id=entry.organization_id,
        pod_id=entry.pod_id,
        user_id=entry.user_id,
        source_type=POD_MACHINE_COMPUTE if entry.billed_to_org else MACHINE_COMPUTE,
        source_id=str(entry.machine_id),
        profile_id="machine",
        profile_scope="SYSTEM",
        model_name=f"machine-{entry.size}",
        usage_kind=UsageKind.COMPUTE.value,
        units=entry.compute_units,
        cost_usd=cost,
        request_id=request_id,
        status="SUCCEEDED",
        record_metadata={
            "session_id": str(entry.session_id),
            "slice_start": entry.slice_start.isoformat(),
            "slice_end": entry.slice_end.isoformat(),
        },
        occurred_at=entry.slice_end,
    )
    try:
        async with uow.session.begin_nested():
            uow.session.add(row)
    except IntegrityError:
        # Written by whoever raced this one; the slice counts once.
        return False
    return True


@dataclass(frozen=True, slots=True)
class ComputeStanding:
    """How far through its allowances one person's or organization's compute is.

    ``fraction`` is the largest of the windows that apply -- the compute
    allowance and the USD limit -- because whichever runs out first is the one
    that stops a machine.
    """

    used_units: float
    allowance_units: float | None
    used_usd: float
    limit_usd: float | None
    resets_at: datetime

    @property
    def fraction(self) -> float | None:
        fractions = []
        if self.allowance_units:
            fractions.append(self.used_units / self.allowance_units)
        if self.limit_usd:
            fractions.append(self.used_usd / self.limit_usd)
        return max(fractions) if fractions else None


def _month(now: datetime) -> tuple[datetime, datetime]:
    month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return month, (month.replace(day=28) + timedelta(days=4)).replace(day=1)


async def resolve_limits(
    uow: SqlAlchemyUnitOfWork, *, organization_id: UUID | None, user_id: UUID
) -> UsageLimitValues:
    from app.modules.usage.services.usage_limit_provider import build_usage_limit_port

    port = build_usage_limit_port(uow)
    if port is None:
        return UsageLimitValues()
    return normalize_limit_values(
        await port.resolve_limits(organization_id=organization_id, user_id=user_id)
    )


async def compute_standing(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    organization_id: UUID | None,
    billed_to_org: bool,
    now: datetime | None = None,
) -> ComputeStanding:
    """This month's compute against the windows a machine's time counts in.

    A pod machine is measured against its organization; a person's own
    machine against the person.
    """
    start, end = _month(now or datetime.now(timezone.utc))
    limits = await resolve_limits(uow, organization_id=organization_id, user_id=user_id)
    units = select(func.coalesce(func.sum(UsageRecord.units), 0.0)).where(
        UsageRecord.usage_kind == UsageKind.COMPUTE.value,
        UsageRecord.occurred_at >= start,
        UsageRecord.occurred_at < end,
    )
    spend = select(
        func.coalesce(
            func.sum(func.coalesce(UsageRecord.cost_amount, UsageRecord.cost_usd)), 0
        )
    ).where(
        UsageRecord.profile_scope == "SYSTEM",
        UsageRecord.occurred_at >= start,
        UsageRecord.occurred_at < end,
    )
    if billed_to_org and organization_id is not None:
        units = units.where(
            UsageRecord.organization_id == organization_id,
            UsageRecord.source_type == POD_MACHINE_COMPUTE,
        )
        spend = spend.where(UsageRecord.organization_id == organization_id)
        allowance, limit = (
            limits.org_monthly_compute_units,
            limits.org_monthly_limit_usd,
        )
    else:
        units = units.where(
            UsageRecord.user_id == user_id,
            UsageRecord.source_type == MACHINE_COMPUTE,
        )
        spend = spend.where(
            UsageRecord.user_id == user_id,
            UsageRecord.source_type.notin_((POD_MACHINE_COMPUTE,)),
        )
        allowance, limit = (
            limits.user_monthly_compute_units,
            limits.user_monthly_limit_usd,
        )
    return ComputeStanding(
        used_units=float(await uow.session.scalar(units) or 0.0),
        allowance_units=allowance,
        used_usd=float(await uow.session.scalar(spend) or 0.0),
        limit_usd=limit,
        resets_at=end,
    )


async def compute_units_by_machine(
    uow: SqlAlchemyUnitOfWork, machine_ids: list[UUID], *, now: datetime | None = None
) -> dict[UUID, float]:
    """Compute each machine used this calendar month, for the Environments rail."""
    if not machine_ids:
        return {}
    start, end = _month(now or datetime.now(timezone.utc))
    rows = await uow.session.execute(
        select(UsageRecord.source_id, func.sum(UsageRecord.units))
        .where(
            UsageRecord.source_type.in_(COMPUTE_SOURCES),
            UsageRecord.source_id.in_([str(m) for m in machine_ids]),
            UsageRecord.occurred_at >= start,
            UsageRecord.occurred_at < end,
        )
        .group_by(UsageRecord.source_id)
    )
    return {UUID(source_id): float(total or 0) for source_id, total in rows.all()}


__all__ = [
    "COMPUTE_SOURCES",
    "ComputeSlice",
    "ComputeStanding",
    "compute_standing",
    "compute_units_by_machine",
    "record_compute_slice",
    "resolve_limits",
]
