"""The contacts cap an organization owner sets, and what counts against it."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.domain.accounting import OUTSIDE_AUDIENCE_SOURCES, money
from app.modules.usage.domain.contacts_cap import StoredContactsCap
from app.modules.usage.infrastructure.cost_expressions import recorded_cost
from app.modules.usage.infrastructure.models import (
    UsageContactsCap,
    UsageLimitCounter,
    UsageRecord,
)

#: The budget window the contacts cap is held in (``budget_windows``).
CONTACTS_WINDOW = "contacts_month"


class UsageContactsCapRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def stored(self, organization_id: UUID) -> StoredContactsCap | None:
        """What the organization's owners set, or ``None`` if they never did."""
        row = await self.session.execute(
            select(UsageContactsCap.monthly_limit_usd).where(
                UsageContactsCap.organization_id == organization_id
            )
        )
        found = row.first()
        return None if found is None else StoredContactsCap(found.monthly_limit_usd)

    async def set_monthly_limit(
        self,
        organization_id: UUID,
        *,
        monthly_limit_usd: Decimal | None,
        updated_by_user_id: UUID,
    ) -> None:
        """Set the cap. ``None`` is no limit, recorded as the owner's choice."""
        statement = insert(UsageContactsCap).values(
            organization_id=organization_id,
            monthly_limit_usd=monthly_limit_usd,
            updated_by_user_id=updated_by_user_id,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[UsageContactsCap.organization_id],
                set_={
                    "monthly_limit_usd": statement.excluded.monthly_limit_usd,
                    "updated_by_user_id": statement.excluded.updated_by_user_id,
                    "updated_at": statement.excluded.updated_at,
                },
            )
        )

    async def spend(
        self, organization_id: UUID, *, start: datetime, end: datetime
    ) -> Decimal:
        """What answering people outside the organization cost it in a window.

        Counted the way the cap's window counts it: system-paid usage under the
        outside-audience sources only.
        """
        total = await self.session.scalar(
            select(func.coalesce(func.sum(recorded_cost()), 0)).where(
                UsageRecord.organization_id == organization_id,
                UsageRecord.profile_scope == "SYSTEM",
                UsageRecord.source_type.in_(OUTSIDE_AUDIENCE_SOURCES),
                UsageRecord.occurred_at >= start,
                UsageRecord.occurred_at < end,
            )
        )
        return money(total or 0)

    async def reserved(self, organization_id: UUID, *, start: datetime) -> Decimal:
        """What runs going now have reserved against this month's cap."""
        total = await self.session.scalar(
            select(func.coalesce(func.sum(UsageLimitCounter.reserved_usd), 0)).where(
                UsageLimitCounter.organization_id == organization_id,
                UsageLimitCounter.user_id.is_(None),
                UsageLimitCounter.window_kind == CONTACTS_WINDOW,
                UsageLimitCounter.window_start == start,
            )
        )
        return money(total or 0)
