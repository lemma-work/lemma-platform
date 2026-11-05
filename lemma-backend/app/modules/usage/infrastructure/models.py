"""Usage SQLAlchemy models."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    event,
    or_,
    select,
    text,
    update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column, object_session

from app.core.infrastructure.db.base import UUIDAuditBase
from app.core.infrastructure.db.transaction_locks import mark_transaction_scoped_lock
from app.modules.usage.domain.accounting import money
from app.modules.usage.domain.entities import UsageKind, UsageProfileScope
from app.modules.usage.domain.entities import UsageRecord as UsageRecordEntity

__all__ = ["UsageLimitCounter", "UsageRecord"]


class UsageRecord(UUIDAuditBase):
    """One compact usage record."""

    __tablename__ = "usage_records"

    # This table gains a row per model call — the highest insert rate in the
    # system — so an index here is paid on every one of them. Fourteen of these
    # columns carried a single-column index; twelve are gone (see migration
    # 0018) because they were either redundant with a composite below that
    # already leads with the same column, or never usable by any query we run.
    # What survives is only what a query actually reads.
    organization_id: Mapped[UUID | None] = mapped_column(nullable=True)
    pod_id: Mapped[UUID | None] = mapped_column(nullable=True)
    user_id: Mapped[UUID] = mapped_column(nullable=False)
    agent_id: Mapped[UUID | None] = mapped_column(nullable=True)
    conversation_id: Mapped[UUID | None] = mapped_column(nullable=True)
    agent_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    parent_agent_run_id: Mapped[UUID | None] = mapped_column(nullable=True)
    source_type: Mapped[str] = mapped_column(String(60), nullable=False)
    source_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    profile_id: Mapped[str] = mapped_column(String(120), nullable=False)
    profile_scope: Mapped[str] = mapped_column(String(32), nullable=False)
    model_name: Mapped[str] = mapped_column(String(160), nullable=False)
    usage_kind: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        default="llm",
    )
    input_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(nullable=False, default=0)
    units: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    cost_usd: Mapped[float | None] = mapped_column(Float, nullable=True)
    request_id: Mapped[UUID | None] = mapped_column(nullable=True)
    cost_amount: Mapped[Decimal | None] = mapped_column(Numeric(24, 9), nullable=True)
    cached_input_tokens: Mapped[int | None] = mapped_column(nullable=True)
    cache_write_tokens: Mapped[int | None] = mapped_column(nullable=True)
    status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    record_metadata: Mapped[dict[str, object] | None] = mapped_column(
        "metadata",
        JSONB,
        default=dict,
        nullable=True,
    )
    # Kept: ``get_system_cost`` may be called with no organization, and then
    # the time range is the only predicate any index can serve.
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    __table_args__ = (
        Index(
            "uq_usage_request_id",
            "request_id",
            unique=True,
            postgresql_where=text("request_id IS NOT NULL"),
        ),
        Index("ix_usage_org_time", "organization_id", "occurred_at"),
        Index("ix_usage_pod_time", "pod_id", "occurred_at"),
        Index("ix_usage_user_time", "user_id", "occurred_at"),
        Index("ix_usage_agent_time", "agent_id", "occurred_at"),
        Index("ix_usage_agent_run_time", "agent_run_id", "occurred_at"),
        Index(
            "ix_usage_org_profile_time", "organization_id", "profile_id", "occurred_at"
        ),
        Index(
            "ix_usage_org_profile_scope_time",
            "organization_id",
            "profile_scope",
            "occurred_at",
        ),
        Index(
            "ix_usage_org_user_profile_scope_time",
            "organization_id",
            "user_id",
            "profile_scope",
            "occurred_at",
        ),
        Index("ix_usage_source_time", "source_type", "source_id", "occurred_at"),
    )

    def to_entity(self) -> UsageRecordEntity:
        return UsageRecordEntity(
            id=self.id,
            created_at=self.created_at,
            updated_at=self.updated_at,
            organization_id=self.organization_id,
            pod_id=self.pod_id,
            user_id=self.user_id,
            agent_id=self.agent_id,
            conversation_id=self.conversation_id,
            agent_run_id=self.agent_run_id,
            parent_agent_run_id=self.parent_agent_run_id,
            source_type=self.source_type,
            source_id=self.source_id,
            profile_id=self.profile_id,
            profile_scope=self.profile_scope,
            model_name=self.model_name,
            usage_kind=self.usage_kind,
            input_tokens=self.input_tokens,
            output_tokens=self.output_tokens,
            units=self.units,
            cost_usd=self.cost_usd,
            cost_amount=self.cost_amount,
            cached_input_tokens=self.cached_input_tokens,
            cache_write_tokens=self.cache_write_tokens,
            status=self.status,
            metadata=self.record_metadata or {},
            occurred_at=self.occurred_at,
        )

    @classmethod
    def from_entity(cls, entity: UsageRecordEntity) -> "UsageRecord":
        usage_kind = (
            entity.usage_kind.value
            if isinstance(entity.usage_kind, UsageKind)
            else str(entity.usage_kind)
        )
        profile_scope = (
            entity.profile_scope.value
            if isinstance(entity.profile_scope, UsageProfileScope)
            else str(entity.profile_scope)
        )
        return cls(
            id=entity.id,
            created_at=entity.created_at,
            updated_at=entity.updated_at,
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
            profile_scope=profile_scope,
            model_name=entity.model_name,
            usage_kind=usage_kind,
            input_tokens=entity.input_tokens,
            output_tokens=entity.output_tokens,
            units=entity.units,
            cost_usd=entity.cost_usd,
            cost_amount=(
                money(entity.cost_amount)
                if entity.cost_amount is not None
                else money(entity.cost_usd)
                if entity.cost_usd is not None
                else None
            ),
            cached_input_tokens=entity.cached_input_tokens,
            cache_write_tokens=entity.cache_write_tokens,
            status=entity.status,
            record_metadata=entity.metadata,
            occurred_at=entity.occurred_at,
        )


class UsageLimitCounter(UUIDAuditBase):
    """System-profile spend in one limit window, kept as a running total.

    Settling a request adds its cost here in the transaction that prices its
    ledger row, so admission reads this rather than summing the ledger.
    ``request_accounting`` describes when the total is recounted from the
    ledger instead, and why each of those cases needs it.
    """

    __tablename__ = "usage_limit_counters"

    organization_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    user_id: Mapped[UUID | None] = mapped_column(index=True, nullable=True)
    window_kind: Mapped[str] = mapped_column(String(32), index=True, nullable=False)
    window_start: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    limit_usd: Mapped[Decimal | None] = mapped_column(Numeric(24, 9), nullable=True)
    warning_emitted: Mapped[bool] = mapped_column(
        default=False, server_default="false", nullable=False
    )
    used_usd: Mapped[Decimal] = mapped_column(
        Numeric(24, 9), nullable=False, default=Decimal(0)
    )
    reserved_usd: Mapped[Decimal] = mapped_column(
        Numeric(24, 9), nullable=False, default=Decimal(0)
    )
    #: The ``BudgetWindow.basis`` ``used_usd`` was last counted under: which
    #: ledger rows it is the total of. Null until it has been counted, and set
    #: back to null by a ledger write that did not add to it.
    basis: Mapped[str | None] = mapped_column(String(64), nullable=True)
    #: When ``used_usd`` was last recounted from the ledger.
    reconciled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        Index(
            "uq_usage_limit_counter_window",
            "organization_id",
            "user_id",
            "window_kind",
            "window_start",
            unique=True,
            postgresql_nulls_not_distinct=True,
        ),
    )


#: The order every writer locks counter rows in, so no two wait on each other
#: in a cycle; ``spend_counters.lock_order`` is the same order in Python.
COUNTER_LOCK_ORDER = (
    UsageLimitCounter.window_kind.collate("C"),
    UsageLimitCounter.window_start,
    UsageLimitCounter.organization_id.asc().nulls_first(),
    UsageLimitCounter.user_id.asc().nulls_first(),
)


#: Held, until its transaction ends, by whoever recounts a counter from the
#: ledger -- and by an insert that marks the counter for recounting, so the
#: mark and a recount never interleave.
COUNTER_RECOUNT_LOCK = text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))")


def counter_recount_lock_key(counter_id: UUID) -> dict[str, str]:
    return {"key": f"usage-counter-recount:{counter_id}"}


@event.listens_for(UsageRecord, "after_insert")
def _uncount_windows_of(
    mapper: Mapper[UsageRecord], connection: Connection, row: UsageRecord
) -> None:
    """Have the windows a priced SYSTEM row falls into recounted before use.

    Request settlement adds its own cost to the counters. Every other writer
    -- the aggregate run recorder, a script, a test -- inserts a row no counter
    knows about, so it marks those counters uncounted in the same transaction,
    and the next admission sums the ledger before trusting them. Settlement
    never reaches this: it prices a row that was inserted unpriced.

    Only a unit-of-work flush (``session.add``) reaches this. An ORM-enabled
    ``insert(UsageRecord)`` or a bulk insert skips mapper events, so a priced
    SYSTEM row written that way must mark the counters itself.

    The mark is a flag, not a charge: a recount that summed before this row
    committed would write its total back as trusted over it. So the counters'
    recount locks are taken first, in lock order. A recount already holding
    one finishes before the mark lands; one that starts later waits for this
    transaction, and its sum includes the row.
    """
    del mapper
    if row.profile_scope != "SYSTEM" or (
        row.cost_amount is None and row.cost_usd is None
    ):
        return
    covering = list(
        connection.scalars(
            select(UsageLimitCounter.id)
            .where(
                UsageLimitCounter.window_start <= row.occurred_at,
                UsageLimitCounter.window_end > row.occurred_at,
                # The member's own windows, scoped to this organization or to none.
                or_(
                    UsageLimitCounter.user_id == row.user_id,
                    UsageLimitCounter.user_id.is_(None),
                ),
                or_(
                    UsageLimitCounter.organization_id == row.organization_id,
                    UsageLimitCounter.organization_id.is_(None),
                ),
                # Never a window of nobody in particular.
                or_(
                    UsageLimitCounter.organization_id.is_not(None),
                    UsageLimitCounter.user_id.is_not(None),
                ),
            )
            .order_by(*COUNTER_LOCK_ORDER)
        )
    )
    if not covering:
        return
    session = object_session(row)
    if session is not None:
        mark_transaction_scoped_lock(session)
    for counter_id in covering:
        connection.execute(COUNTER_RECOUNT_LOCK, counter_recount_lock_key(counter_id))
    connection.execute(
        update(UsageLimitCounter)
        .where(
            UsageLimitCounter.id.in_(
                select(UsageLimitCounter.id)
                .where(
                    UsageLimitCounter.id.in_(covering),
                    UsageLimitCounter.basis.is_not(None),
                )
                .order_by(*COUNTER_LOCK_ORDER)
                .with_for_update()
            )
        )
        .values(basis=None)
    )


class UsageContactsCap(UUIDAuditBase):
    """What an organization lets its bots spend answering contacts, a month.

    Set by an organization owner. No row means the deployment's default
    (``usage_contacts_monthly_default_usd``); a row with no limit means the
    owner removed the cap, which the default never overrides. Contacts are
    never billed, so this is the only ceiling on what people outside the
    organization can cost it, apart from the turn rate limits. Goes with the
    organization.
    """

    __tablename__ = "usage_contacts_caps"
    __table_args__ = (
        Index("uq_usage_contacts_cap_org", "organization_id", unique=True),
    )

    organization_id: Mapped[UUID] = mapped_column(
        ForeignKey("organizations.id", ondelete="CASCADE"), nullable=False
    )
    monthly_limit_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(24, 9), nullable=True
    )
    #: Whether an owner chose ``monthly_limit_usd``; otherwise the default holds.
    monthly_limit_set: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )
    #: How many contacts the organization's bots may answer; null for no limit.
    max_contacts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    #: Whether an owner chose ``max_contacts``; otherwise the default holds.
    max_contacts_set: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    updated_by_user_id: Mapped[UUID | None] = mapped_column(nullable=True)
