"""Whether an organization may still answer people outside it this month.

Asked before a run for a contact or a group's outsider is started, by the
surface that would start it: past the cap, no run starts at all, so nothing is
reserved, nothing fails half way, and the person is told a member will reply.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.usage.services.contacts_cap import contacts_cap_status, this_month

__all__ = ["contacts_cap_reached", "this_month"]


async def contacts_cap_reached(
    uow: SqlAlchemyUnitOfWork, *, organization_id: UUID
) -> bool:
    """Whether this month's spend answering outsiders has reached the cap."""
    return (await contacts_cap_status(uow, organization_id)).reached
