"""What the contacts module asks of the channels when it forgets somebody."""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.infrastructure.contact_channels import ContactChannels


async def forget_contacts_in_channels(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_ids: Collection[UUID]
) -> int:
    """Take these contacts out of the pod's channels, in the transaction that
    deletes them: their own channel goes, and their lines anywhere else are
    emptied in place, so nobody else's reply, answer or thread goes with
    them. Returns how many lines elsewhere were emptied."""
    return await ContactChannels(uow.session).forget(
        pod_id=pod_id, contact_ids=contact_ids
    )


__all__ = ["forget_contacts_in_channels"]
