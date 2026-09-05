"""Usage-owned spending authority; execution rows contain no accounting state."""

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import DateTime, Enum, Index, Numeric, Uuid, String
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import UUIDAuditBase
from app.modules.usage.domain.accounting import AllocationState


class UsageAllocation(UUIDAuditBase):
    __tablename__ = "usage_allocations"

    identity: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    pricing: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    counter_ids: Mapped[list[UUID]] = mapped_column(ARRAY(Uuid), nullable=False)
    allocated: Mapped[Decimal] = mapped_column(Numeric(24, 9), nullable=False)
    remaining: Mapped[Decimal] = mapped_column(Numeric(24, 9), nullable=False)
    uncertain: Mapped[Decimal] = mapped_column(
        Numeric(24, 9), default=Decimal(0), nullable=False
    )
    limited: Mapped[bool] = mapped_column(nullable=False)
    last_receipt_digest: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sequence: Mapped[int] = mapped_column(default=0, nullable=False)
    state: Mapped[AllocationState] = mapped_column(
        Enum(AllocationState, native_enum=False, length=20),
        default=AllocationState.ACTIVE,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    window_end: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    __table_args__ = (Index("ix_usage_allocations_recovery", "state", "expires_at"),)
