"""The contacts cap an organization admin sets."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.usage.domain.accounting import OUTSIDE_AUDIENCE_SOURCES, money
from app.modules.usage.infrastructure.cost_expressions import recorded_cost
from app.modules.usage.infrastructure.models import UsageContactsCap, UsageRecord


class UsageContactsCapRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def monthly_limit(self, organization_id: UUID) -> Decimal | None:
        """The organization's cap, or ``None`` when it has set none."""
        return await self.session.scalar(
            select(UsageContactsCap.monthly_limit_usd).where(
                UsageContactsCap.organization_id == organization_id
            )
        )

    async def set_monthly_limit(
        self,
        organization_id: UUID,
        *,
        monthly_limit_usd: Decimal | None,
        updated_by_user_id: UUID,
    ) -> None:
        """Set or clear the cap. ``None`` removes it."""
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
