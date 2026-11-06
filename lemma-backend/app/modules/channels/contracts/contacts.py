"""What the contacts module asks of the channels: where a contact's own chat
is, and what forgetting somebody does to them."""

from __future__ import annotations

from collections.abc import Collection
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.infrastructure.contact_channels import ContactChannels


async def contact_channel_ids(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, contact_ids: Collection[UUID]
) -> dict[UUID, UUID]:
    """Each contact's own channel in the pod, by contact, for those who have
    one -- a contact who never wrote has none. One read for a page of
    contacts, so a list can open each one's chat without looking it up."""
    return await ContactChannels(uow.session).own_channel_ids(
        pod_id=pod_id, contact_ids=contact_ids
    )


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


__all__ = ["contact_channel_ids", "forget_contacts_in_channels"]
