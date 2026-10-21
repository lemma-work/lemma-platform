"""Whether an organization may still answer people outside it this month.

Asked before a run for a contact or a group's outsider is started, by the
surface that would start it: past the cap, no run starts at all, so nothing is
reserved, nothing fails half way, and the person is told a member will reply.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.usage.domain.contacts_cap import may_answer_another_contact
from app.modules.usage.services.contacts_cap import (
    applicable_contacts_cap,
    contacts_cap_status,
    this_month,
)

__all__ = [
    "contacts_cap_reached",
    "counts_contacts",
    "room_for_another_contact",
    "this_month",
]


async def contacts_cap_reached(
    uow: SqlAlchemyUnitOfWork, *, organization_id: UUID
) -> bool:
    """Whether this month's spend answering outsiders has reached the cap."""
    return (await contacts_cap_status(uow, organization_id)).reached


async def counts_contacts(uow: SqlAlchemyUnitOfWork, *, organization_id: UUID) -> bool:
    """Whether the organization's cap limits how many contacts may be answered."""
    cap = await applicable_contacts_cap(uow, organization_id)
    return cap.max_contacts is not None


async def room_for_another_contact(
    uow: SqlAlchemyUnitOfWork, *, organization_id: UUID, answered: int
) -> bool:
    """Whether a contact nobody has answered yet may be, with ``answered`` so far."""
    cap = await applicable_contacts_cap(uow, organization_id)
    return may_answer_another_contact(cap, answered=answered)
