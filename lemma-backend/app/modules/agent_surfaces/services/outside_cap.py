"""Past the contacts cap: no run, a person is told, and a member takes over.

Once an organization has spent its month's cap on answering people outside it,
a run for one of them would only be refused by metering half way through
setup, and the person would read "I couldn't finish that request". So the
surface asks first, before any run starts, and when the cap is reached:

* **No run.** Their message is kept in the conversation, where the member who
  looks after it reads it.
* **They are told once a day** that a person from the pod will reply -- not on
  every message, which would be the bot talking to itself at them.
* **The member is told** in their inbox, on the same once a day.
* **The conversation is handed to the member** until the month the cap counts
  ends (``agent.domain.outsiders.HANDED_TO_KEY``), so the bot stays quiet in it
  even if the cap is raised mid-thread: someone is answering by hand now.

Used by every place a run for somebody outside the pod starts: the chat and
email surfaces (``SurfaceTurnStarter``) and web chat. All of it is in the
caller's unit of work, so the message, the hand-off and the note commit
together.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.contact_conversations import (
    conversation_handed_to,
    hold_outside_turn,
)
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
)
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (  # noqa: E501
    NotificationRepository,
)
from app.modules.agent_surfaces.services.contact_windows import ContactWindows
from app.modules.agent_surfaces.services.outside_questions import quote
from app.modules.contacts.contracts import (
    answered_contacts_in_organization,
    contact_by_id,
    lock_answered_contacts_count,
    note_answered,
)
from app.modules.pod.contracts.members import (
    pod_member_id,
    pod_name,
    pod_organization_id,
)
from app.modules.usage.contracts.contacts_cap import (
    contacts_cap_reached,
    counts_contacts,
    room_for_another_contact,
    this_month,
)

logger = get_logger(__name__)

#: The most of their message the member's note quotes.
_NOTE_QUOTE_CHARS = 500


def person_will_reply(space_name: str | None) -> str:
    return f"A person from {space_name or 'the team'} will reply here."


@dataclass(frozen=True, slots=True)
class HeldTurn:
    """A turn that started no run. ``reply`` is what to tell them now, if anything."""

    reply: str | None


async def held_for_a_person(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    conversation_id: UUID,
    now: datetime | None = None,
) -> bool:
    """Whether this contact's turn must wait for a member rather than run.

    It must while a member has the conversation, while the organization's
    contacts cap is reached, and when the contact would be one more than the
    organization's bots may answer. A turn that may run marks its contact
    answered, in the same transaction, so the count it was let in under holds.
    """
    now = now or datetime.now(timezone.utc)
    if await conversation_handed_to(uow, conversation_id, now=now) is not None:
        return True
    organization_id = await pod_organization_id(uow, pod_id)
    if organization_id is None:
        return False
    if await contacts_cap_reached(uow, organization_id=organization_id):
        return True
    conversation = await agent_conversations.surface_conversation(uow, conversation_id)
    contact_id = conversation.audience.contact_id if conversation else None
    if contact_id is None:
        return False
    if not await _admitted(uow, organization_id, pod_id, contact_id):
        logger.info(
            "agent_surfaces.outside_cap.contact_limit_reached.observed",
            pod_id=str(pod_id),
            contact_id=str(contact_id),
        )
        return True
    return False


async def _admitted(
    uow: SqlAlchemyUnitOfWork, organization_id: UUID, pod_id: UUID, contact_id: UUID
) -> bool:
    """Whether this contact may be answered: already was, or the count has room
    -- in which case they take their place in it now.

    The contact a merged id leads to is the one counted and marked. A contact
    not yet answered is admitted under a lock on the organization's count, so
    two arriving together cannot both take its last place, and two turns of one
    contact take one. One answered before takes no lock, and neither does any
    contact where the cap keeps no count: there is no place to take.
    """
    contact = await contact_by_id(uow, pod_id=pod_id, contact_id=contact_id)
    if contact is not None and contact.answered:
        return True
    counted = contact.id if contact is not None else contact_id
    if not await counts_contacts(uow, organization_id=organization_id):
        await note_answered(uow, pod_id=pod_id, contact_id=counted)
        return True
    await lock_answered_contacts_count(uow, organization_id=organization_id)
    # Another turn of theirs may have taken their place while this one waited.
    now = await contact_by_id(uow, pod_id=pod_id, contact_id=counted)
    if now is not None and now.answered:
        return True
    answered = await answered_contacts_in_organization(
        uow, organization_id=organization_id
    )
    if not await room_for_another_contact(
        uow, organization_id=organization_id, answered=answered
    ):
        return False
    await note_answered(uow, pod_id=pod_id, contact_id=counted)
    return True


async def first_word_today(
    conversation_id: UUID, *, windows: ContactWindows | None = None
) -> bool:
    """Claim today's one "a person will reply" for this conversation.

    Asked before the unit of work is opened, so no connection is held while
    Redis answers; pass the answer to ``hand_to_a_person`` as ``tell``.
    """
    return await (windows or ContactWindows()).person_will_reply(conversation_id)


async def hand_to_a_person(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    conversation_id: UUID,
    owner_id: UUID,
    text: str,
    metadata: dict[str, object],
    tell: bool,
    now: datetime | None = None,
) -> HeldTurn:
    """Keep their message, hand the conversation over, and say so once a day.

    ``owner_id`` is the member who looks after the conversation: the hand-off
    is to them, and so is the note. ``tell`` is ``first_word_today``'s answer:
    whether this is the turn that tells the person, and the member, today.
    """
    now = now or datetime.now(timezone.utc)
    member = await conversation_handed_to(uow, conversation_id, now=now) or owner_id
    first_today = tell
    space = await pod_name(uow.session, pod_id)
    reply = person_will_reply(space) if first_today else None
    _, month_end = this_month(now)
    await hold_outside_turn(
        uow,
        conversation_id=conversation_id,
        text=text,
        metadata=metadata,
        hand_to=member,
        until=month_end,
        reply=reply,
    )
    if first_today:
        await _tell_member(
            uow,
            pod_id=pod_id,
            member=member,
            conversation_id=conversation_id,
            text=text,
        )
    logger.info(
        "agent_surfaces.outside_cap.turn_held.observed",
        pod_id=str(pod_id),
        conversation_id=str(conversation_id),
        told=first_today,
    )
    return HeldTurn(reply=reply)


async def _tell_member(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    member: UUID,
    conversation_id: UUID,
    text: str,
) -> None:
    """An inbox note pointing at the conversation, so forgetting them redacts it."""
    member_id = await pod_member_id(uow, pod_id, member)
    if member_id is None:
        return
    await NotificationRepository(uow).create(
        NotificationEntity(
            pod_id=pod_id,
            recipient_user_id=member,
            recipient_pod_member_id=member_id,
            origin_kind=NotificationOriginKind.API,
            origin_conversation_id=conversation_id,
            title="Somebody outside the space is waiting for you",
            body=(
                "The bot has stopped answering people outside the space for "
                "now: the organization's monthly cap on answering them is "
                "reached. They were told a person will reply, and the "
                "conversation is yours to answer. They wrote:\n\n"
                + quote(text, limit=_NOTE_QUOTE_CHARS)
            ),
            expects_response=False,
            delivery_status=NotificationDeliveryStatus.UNDELIVERABLE,
        )
    )
