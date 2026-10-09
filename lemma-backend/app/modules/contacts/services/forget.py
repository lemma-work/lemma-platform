"""Forgetting a contact: everything the pod holds about them, in an order that can be retried.

Two databases hold a contact. Their rows in the pod's contact-owned tables live
in the pod's own database, which shares no transaction with the main one, so
those go first: if that fails, nothing else has been touched, and asking again
starts over. Everything in the main database then goes in one transaction --
their conversations, their handles, the web chat sessions that named them, the
platform profiles stored for their handles -- so the contact is never half
forgotten there. The questions of theirs a bot passed on to a member are blanked
in that same transaction, before the conversations they point at are deleted.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts.contact_conversations import (
    contact_conversation_ids,
    forget_contact_conversations,
)
from app.modules.agent_surfaces.contracts.contacts import (
    forget_contact_senders,
    redact_questions_from,
)
from app.modules.contacts.contracts.visitor_sessions import (
    forget_contact_visitor_sessions,
    forget_session_liveness,
)
from app.modules.contacts.infrastructure.repository import ContactRepository
from app.modules.datastore.contracts.contact_rows import delete_contact_rows

logger = get_logger(__name__)


#: Conversations read at a time while blanking the questions asked in them.
_REDACT_PAGE = 500


async def _redact_their_questions(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, contact_id: UUID
) -> int:
    """Blank what of theirs reached members, a page of conversations at a time."""
    redacted, after = 0, None
    while True:
        page = await contact_conversation_ids(
            uow, pod_id=pod_id, contact_id=contact_id, after=after, limit=_REDACT_PAGE
        )
        redacted += await redact_questions_from(uow, conversation_ids=page)
        if len(page) < _REDACT_PAGE:
            return redacted
        after = page[-1]


@dataclass(frozen=True, slots=True)
class Forgotten:
    rows: int
    conversations: int
    web_sessions: int
    senders: int
    notifications: int


async def forget_contact(
    uow_factory: UnitOfWorkFactory,
    *,
    pod_id: UUID,
    contact_id: UUID,
    forgotten_by: UUID,
) -> Forgotten | None:
    """Forget this contact, or ``None`` when the pod has no such contact."""
    async with uow_factory() as uow:
        contact = await ContactRepository(uow.session).get(
            pod_id=pod_id, contact_id=contact_id
        )
    if contact is None:
        return None
    rows = await delete_contact_rows(uow_factory, pod_id=pod_id, contact_id=contact_id)
    async with uow_factory() as uow:
        notifications = await _redact_their_questions(uow, pod_id, contact_id)
        conversations = await forget_contact_conversations(
            uow, pod_id=pod_id, contact_id=contact_id
        )
        ended = await forget_contact_visitor_sessions(uow, contact_id=contact_id)
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
        notifications=notifications,
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
        notifications=forgotten.notifications,
    )
    return forgotten
