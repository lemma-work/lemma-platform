"""Forgetting a contact: everything the pod holds about them, in an order that can be retried.

Two databases hold a contact. Their rows in the pod's contact-owned tables live
in the pod's own database, which shares no transaction with the main one, so
those go first: if that fails, nothing else has been touched, and asking again
starts over. Everything in the main database then goes in one transaction --
their conversations, their handles, the web chat sessions that named them, the
platform profiles stored for their handles -- so the contact is never half
forgotten there. The questions of theirs a bot passed on to a member are blanked
in that same transaction, before the conversations they point at are deleted.

In the channels, their own channel goes with them; in every other -- a group
they wrote in -- their lines are emptied in place rather than deleted, so the
members' replies, the agents' answers and the threads they started stay, and
the timeline keeps its numbers (``channels/contracts/contacts``).

A merged contact's id leads to the person it became, here as in every other
verb: forgetting it forgets that person.

A contact others were merged into is forgotten with them. Their rows and
conversations move to it in queued work after the merge, so a forget that comes
first finds them still under the merged ids -- and deleting the contact cascades
to those ids, which would leave the rows naming nobody, out of any later
forget's reach.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts.contacts import (
    forget_contact_conversations,
)
from app.modules.agent_surfaces.contracts.contacts import forget_contact_senders
from app.modules.channels.contracts.contacts import forget_contacts_in_channels
from app.modules.contacts.contracts.visitor_sessions import (
    forget_contact_visitor_sessions,
    forget_session_liveness,
)
from app.modules.contacts.infrastructure.repository import (
    ABSORBED_PAGE,
    ContactRepository,
)
from app.modules.datastore.contracts.contact_rows import delete_contact_rows

logger = get_logger(__name__)


async def _absorbed_by(
    uow_factory: UnitOfWorkFactory, pod_id: UUID, contact_id: UUID
) -> list[UUID]:
    """Every contact merged into this one, a page at a time: each must be
    forgotten before the cascade takes its row."""
    absorbed: list[UUID] = []
    async with uow_factory() as uow:
        contacts = ContactRepository(uow.session)
        while True:
            page = await contacts.absorbed_by(
                pod_id=pod_id,
                contact_id=contact_id,
                after=absorbed[-1] if absorbed else None,
            )
            absorbed += page
            if len(page) < ABSORBED_PAGE:
                return absorbed


@dataclass(frozen=True, slots=True)
class Forgotten:
    rows: int
    conversations: int
    web_sessions: int
    senders: int


async def forget_contact(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    contact_id: UUID,
    forgotten_by: UUID,
) -> Forgotten | None:
    """Forget this contact, or ``None`` when the pod has no such contact."""
    async with uow_factory() as uow:
        contacts = ContactRepository(uow.session)
        contact = await contacts.get(pod_id=pod_id, contact_id=contact_id)
        if contact is not None and contact.merged_into_id is not None:
            contact = await contacts.get(
                pod_id=pod_id, contact_id=contact.merged_into_id
            )
    if contact is None:
        return None
    contact_id = contact.id
    everyone = [contact_id, *await _absorbed_by(uow_factory, pod_id, contact_id)]
    rows = 0
    for one in everyone:
        rows += await delete_contact_rows(uow_factory, pod_id=pod_id, contact_id=one)
    async with uow_factory() as uow:
        # Channels first: their locks are taken before the lines under them,
        # as every writer to a channel takes them.
        await forget_contacts_in_channels(uow, pod_id=pod_id, contact_ids=everyone)
        conversations = 0
        ended: list[UUID] = []
        for one in everyone:
            conversations += await forget_contact_conversations(
                uow, pod_id=pod_id, contact_id=one
            )
            ended += await forget_contact_visitor_sessions(uow, contact_id=one)
        senders = await forget_contact_senders(
            uow,
            pod_id=pod_id,
            handles=[(handle.kind, handle.value) for handle in contact.identities],
        )
        await ContactRepository(uow.session).delete(
            pod_id=pod_id, contact_id=contact_id
        )
    # After the commit: a session's cached "live" lapses on its own, so a
    # failure here leaves nothing open for longer than that.
    await forget_session_liveness(ended)
    forgotten = Forgotten(
        rows=rows,
        conversations=conversations,
        web_sessions=len(ended),
        senders=senders,
    )
    logger.info(
        "contacts.forget.contact_forgotten.observed",
        pod_id=str(pod_id),
        contact_id=str(contact_id),
        forgotten_by_user_id=str(forgotten_by),
        rows=forgotten.rows,
        conversations=forgotten.conversations,
        web_sessions=forgotten.web_sessions,
        senders=forgotten.senders,
    )
    return forgotten
