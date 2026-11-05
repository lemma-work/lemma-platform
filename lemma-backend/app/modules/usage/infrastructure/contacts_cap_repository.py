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
from app.modules.usage.infrastructure.models import UsageContactsCap, UsageRecord


class UsageContactsCapRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def stored(self, organization_id: UUID) -> StoredContactsCap | None:
        """What the organization's owners set, or ``None`` if they never did."""
        row = await self.session.execute(
            select(
                UsageContactsCap.monthly_limit_usd,
                UsageContactsCap.max_contacts,
                UsageContactsCap.monthly_limit_set,
                UsageContactsCap.max_contacts_set,
            ).where(UsageContactsCap.organization_id == organization_id)
        )
        found = row.first()
        if found is None:
            return None
        return StoredContactsCap(
            monthly_limit_usd=found.monthly_limit_usd,
            max_contacts=found.max_contacts,
            monthly_limit_set=found.monthly_limit_set,
            max_contacts_set=found.max_contacts_set,
        )

    async def set_cap(
        self,
        organization_id: UUID,
        cap: StoredContactsCap,
        *,
        updated_by_user_id: UUID,
    ) -> None:
        """Set the cap. ``None`` in a half the owner set is no limit, recorded
        as their choice; a half they did not set follows the default.

        The month's running total needs no mark: settlement charges the
        ``contacts_month`` counter whether or not a cap applies, so a cap
        removed and set back to what it was finds it exact.
        """
        statement = insert(UsageContactsCap).values(
            organization_id=organization_id,
            monthly_limit_usd=cap.monthly_limit_usd,
            monthly_limit_set=cap.monthly_limit_set,
            max_contacts=cap.max_contacts,
            max_contacts_set=cap.max_contacts_set,
            updated_by_user_id=updated_by_user_id,
        )
        await self.session.execute(
            statement.on_conflict_do_update(
                index_elements=[UsageContactsCap.organization_id],
                set_={
                    "monthly_limit_usd": statement.excluded.monthly_limit_usd,
                    "monthly_limit_set": statement.excluded.monthly_limit_set,
                    "max_contacts": statement.excluded.max_contacts,
                    "max_contacts_set": statement.excluded.max_contacts_set,
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
        outside-audience sources only. Summed for the owners' page; whether the
        cap is reached is read from the window's running total.
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
