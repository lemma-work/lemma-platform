"""Where an organization stands against its contacts cap this month."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.usage.config import UsageSettings, usage_settings
from app.modules.usage.domain.contacts_cap import (
    ContactsCap,
    cap_reached,
    effective_contacts_cap,
)
from app.modules.usage.infrastructure.contacts_cap_repository import (
    UsageContactsCapRepository,
)


@dataclass(frozen=True, slots=True)
class ContactsCapStatus:
    cap: ContactsCap
    spent_usd: Decimal
    reserved_usd: Decimal

    @property
    def reached(self) -> bool:
        return cap_reached(self.cap, consumed_usd=self.spent_usd + self.reserved_usd)


def this_month(now: datetime) -> tuple[datetime, datetime]:
    """The calendar month (UTC) the cap counts, as ``[start, end)``."""
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return start, (start.replace(day=28) + timedelta(days=4)).replace(day=1)


async def applicable_contacts_cap(
    uow: SqlAlchemyUnitOfWork,
    organization_id: UUID,
    *,
    settings: UsageSettings | None = None,
) -> ContactsCap:
    stored = await UsageContactsCapRepository(uow.session).stored(organization_id)
    default = (settings or usage_settings).usage_contacts_monthly_default_usd
    return effective_contacts_cap(stored, default_usd=default)


async def contacts_cap_status(
    uow: SqlAlchemyUnitOfWork,
    organization_id: UUID,
    *,
    settings: UsageSettings | None = None,
    now: datetime | None = None,
) -> ContactsCapStatus:
    """The cap that applies, what was spent under it, and what is reserved now."""
    start, end = this_month(now or datetime.now(timezone.utc))
    caps = UsageContactsCapRepository(uow.session)
    return ContactsCapStatus(
        cap=await applicable_contacts_cap(uow, organization_id, settings=settings),
        spent_usd=await caps.spend(organization_id, start=start, end=end),
        reserved_usd=await caps.reserved(organization_id, start=start),
    )
