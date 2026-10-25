"""Past the contacts cap: no run, a person is told, and a member takes over.

Once an organization has spent its month's cap on answering people outside it,
a run for one of them would only be refused by metering half way through
setup, and the person would read "I couldn't finish that request". So the
surface asks first, before any run starts, and when the cap is reached:

* **No run.** Their message is kept in the conversation, where the member who
  looks after it reads it.
* **The member is told**, pushed, once a day -- or, when the member who looks
  after them cannot open a contact's channel, the pod's administrators are
  too, and the member is told how to get access rather than pointed at a
  channel they cannot open.
* **They are told once a day** that a person from the pod will reply -- not on
  every message, which would be the bot talking to itself at them -- and only
  when somebody who can open their channel was told. It is the pod's line at
  their place, sent as a delivery like any other: their rules apply, and an
  email carries the unsubscribe footer and headers.

A member can also answer a contact by hand, whatever the cap
(``contact_hold``): while the contact's channel has its agents off, or a member
has stepped in there, no run starts for them either -- and they are not told
"a person will reply" here, since one is, or has chosen to. The pod's own
acknowledgement may be said instead, once per wait
(``contact_acknowledgement``), and the channel asks for a reply until a
member writes.

Used by every place a run for somebody outside the pod starts: the chat and
email surfaces (``SurfaceTurnStarter``) and web chat. All of it is in the
caller's unit of work, so the message, the hand-off and the note commit
together -- except whether the cap is reached, which is asked first, in a unit
of its own (``organization_capped``).
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.contact_conversations import hold_outside_turn
from app.modules.agent.contracts import channel_conversations
from app.modules.agent_surfaces.services.contact_acknowledgement import (
    acknowledge_contact,
)
from app.modules.agent_surfaces.services.direct_messages import tell_member
from app.modules.agent_surfaces.services.contact_windows import ContactWindows
from app.modules.agent_surfaces.services.contact_hold import (
    MemberHold,
    held_by_a_member,
)
from app.modules.agent_surfaces.services.channel_lines import WEB, shown_on_web
from app.modules.channels.contracts import (
    ChannelKind,
    NewLine,
    Principal,
    Sender,
    call_for_person,
    channel_by_id,
    post,
    sources_of,
)
from app.modules.contacts.contracts import (
    answered_contacts_in_organization,
    contact_by_id,
    lock_answered_contacts_count,
    note_answered,
)
from app.modules.pod.contracts.members import (
    pod_administrators,
    pod_member_holds,
    pod_member_id,
    pod_name,
    pod_organization_id,
)
from app.modules.usage.contracts.contacts_cap import (
    contacts_cap_reached,
    counts_contacts,
    room_for_another_contact,
)

logger = get_logger(__name__)


def person_will_reply(space_name: str | None) -> str:
    return f"A person from {space_name or 'the team'} will reply here."


@dataclass(frozen=True, slots=True)
class HeldTurn:
    """A turn that started no run. ``reply`` is what to tell them now, if anything."""

    reply: str | None


async def organization_capped(uow_factory: UnitOfWorkFactory, *, pod_id: UUID) -> bool:
    """Whether the pod's organization has spent its contacts cap this month.

    Asked in a unit of work of its own, committed before the turn's. The
    answer is read from the running total the run's requests are held to, and
    a recount of it holds the total's row until its unit ends. A turn starting
    elsewhere reads that total while holding its conversation's lock or its
    organization's count of contacts -- the locks the turn's own unit goes on
    to take -- so holding the total across them could deadlock with it.
    Committing here also keeps what a recount learned, whatever becomes of the
    turn.
    """
    async with uow_factory() as uow:
        organization_id = await pod_organization_id(uow, pod_id)
        capped = organization_id is not None and await contacts_cap_reached(
            uow, organization_id=organization_id
        )
        await uow.commit()
    return capped


async def held_for_a_person(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    conversation_id: UUID,
    capped: bool,
) -> bool:
    """Whether this contact's turn must wait for a member rather than run.

    It must while a member answers their channel by hand, while the
    organization's contacts cap is reached (``capped``, what
    ``organization_capped`` answered), and when the contact would be one more
    than the organization's bots may answer. A turn that may run marks its
    contact answered, in the same transaction, so the count it was let in under
    holds.
    """
    if capped or await held_by_a_member(uow, conversation_id) is not None:
        return True
    organization_id = await pod_organization_id(uow, pod_id)
    if organization_id is None:
        return False
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
) -> HeldTurn:
    """Keep their message for a person, and say so once a day.

    The person is the member answering the channel by hand, or else
    ``owner_id``, the member who looks after the conversation; the note is
    theirs. ``tell`` is ``first_word_today``'s answer: whether this is the turn
    that tells the person, and the member, today.
    """
    member_hold = await held_by_a_member(uow, conversation_id)
    if member_hold is not None:
        await _keep_for_a_member(
            uow,
            pod_id=pod_id,
            conversation_id=conversation_id,
            owner_id=owner_id,
            text=text,
            metadata=metadata,
            hold=member_hold,
            tell=tell,
        )
        return HeldTurn(reply=None)
    member = owner_id
    first_today = tell
    told = (
        await _tell_member(
            uow,
            pod_id=pod_id,
            member=member,
            conversation_id=conversation_id,
            text=text,
        )
        if first_today
        else False
    )
    space = await pod_name(uow.session, pod_id)
    # Promised only when a member who can open their channel was told.
    reply = person_will_reply(space) if told else None
    await hold_outside_turn(
        uow,
        conversation_id=conversation_id,
        text=text,
        metadata=metadata,
        reply=reply,
    )
    carried = reply is not None and await _courtesy_line(
        uow, pod_id=pod_id, conversation_id=conversation_id, text=reply
    )
    logger.info(
        "agent_surfaces.outside_cap.turn_held.observed",
        pod_id=str(pod_id),
        conversation_id=str(conversation_id),
        told=told,
    )
    # A line with its delivery goes by itself; only a place with no contact's
    # channel (somebody outside the pod in a group) is answered by the caller.
    return HeldTurn(reply=None if carried else reply)


async def _courtesy_line(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, conversation_id: UUID, text: str
) -> bool:
    """Say ``text`` to the contact as the pod's line at the place they wrote
    from, carried by its delivery like any line: the contact's rules are held
    to it, and an email carries the footer and ``List-Unsubscribe``. Whether
    it was posted -- ``False`` outside a contact's channel."""
    channel_id = await channel_conversations.channel_of_conversation(
        uow, conversation_id
    )
    channel = await channel_by_id(uow, channel_id) if channel_id else None
    if (
        channel is None
        or channel.kind is not ChannelKind.CONTACT
        or channel.contact_id is None
    ):
        return False
    heard = [
        source
        for source in await sources_of(uow, channel.id)
        if not source.is_pending and source.last_inbound_at is not None
    ]
    if not heard:
        return False
    source = heard[0]
    conversation = await agent_conversations.surface_conversation(uow, conversation_id)
    line = await post(
        uow,
        channel_id=channel.id,
        line=NewLine(
            sender=Sender.agent(
                effective_agent_id(
                    conversation.agent_id if conversation is not None else None,
                    pod_id=pod_id,
                ),
                on_behalf_of=Principal.contact(channel.contact_id),
            ),
            text=text,
            target_source_id=source.id,
            conversation_id=conversation_id,
        ),
        deliver_to=[source.id],
    )
    if line is not None and source.platform == WEB:
        await shown_on_web(uow, line, source)
    return True


async def _keep_for_a_member(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    conversation_id: UUID,
    owner_id: UUID,
    text: str,
    metadata: dict[str, object],
    hold: MemberHold,
    tell: bool,
) -> None:
    """Their message, kept for the member answering them by hand.

    Either way the channel asks for a reply until a member writes, and the
    contact is told -- once until a member writes -- what the pod set to say
    then, if it set anything (``contact_acknowledgement``, which says it to a
    member's contact only before that member first wrote to them). A member
    who stepped in is pushed it as the one the contact is assigned to; with
    the agents off, the member who looks after them is told once a day.
    """
    acknowledged = await acknowledge_contact(
        uow, pod_id=pod_id, conversation_id=conversation_id
    )
    await hold_outside_turn(
        uow,
        conversation_id=conversation_id,
        text=text,
        metadata=metadata,
        reply=acknowledged,
    )
    channel_id = await channel_conversations.channel_of_conversation(
        uow, conversation_id
    )
    if channel_id is not None:
        await call_for_person(
            uow,
            channel_id=channel_id,
            reason=(
                "Waiting on a reply from the member answering them"
                if hold is MemberHold.STEPPED_IN
                else "Waiting on a reply: the agent is off"
            ),
        )
    if hold is MemberHold.STEPPED_IN:
        return
    if tell and await pod_member_id(uow, pod_id, owner_id) is not None:
        await tell_member(
            uow,
            pod_id=pod_id,
            user_id=owner_id,
            text=(
                "A contact you look after wrote, and the agent is off in their "
                "channel: they are waiting on a reply from you. "
                + await _where_to_answer(uow, pod_id, owner_id)
            ),
            urgent=True,
        )


async def _tell_member(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    member: UUID,
    conversation_id: UUID,
    text: str,
) -> bool:
    """Flag the contact's channel as waiting for a person, and tell the
    member -- pushed, since nobody else will answer. Whether somebody who can
    open the channel was told: the member, or else the pod's administrators
    when the member may not see contacts.

    The note says where to look, not what they wrote: their words are in
    their own channel, which forgetting them takes with it.
    """
    del text
    if await pod_member_id(uow, pod_id, member) is None:
        return False
    channel_id = await channel_conversations.channel_of_conversation(
        uow, conversation_id
    )
    if channel_id is not None:
        await call_for_person(
            uow, channel_id=channel_id, reason="The monthly cap on answering contacts"
        )
    waiting = (
        "Somebody outside the space is waiting for you. The bot has stopped "
        "answering people outside the space for now: the organization's "
        "monthly cap on answering them is reached. "
    )
    if await pod_member_holds(uow, pod_id, member, _SEES_CONTACTS):
        await tell_member(
            uow,
            pod_id=pod_id,
            user_id=member,
            text=waiting + "They were told a person will reply -- their channel is in "
            "Contacts, waiting on a person.",
            urgent=True,
        )
        return True
    # They look after the chat but cannot open a contact's channel: pointing
    # them at one would be a dead end. Somebody who can is told too.
    administrators = [
        admin.user_id
        for admin in await pod_administrators(uow, pod_id)
        if admin.user_id != member
    ]
    await tell_member(
        uow,
        pod_id=pod_id,
        user_id=member,
        text=waiting + _CANNOT_OPEN,
        urgent=True,
    )
    for admin in administrators:
        await tell_member(
            uow,
            pod_id=pod_id,
            user_id=admin,
            text=waiting.replace("waiting for you", "waiting for a reply")
            + "The member who looks after them cannot open contacts' channels, "
            "so it is yours: their channel is in Contacts, waiting on a person.",
            urgent=True,
        )
    return bool(administrators)


#: Who may open a contact's channel.
_SEES_CONTACTS = "contact.read"

_CANNOT_OPEN = (
    "You look after them but cannot open contacts' channels in this pod: ask "
    "a pod admin to make you an editor, or to answer them."
)


async def _where_to_answer(
    uow: SqlAlchemyUnitOfWork, pod_id: UUID, member: UUID
) -> str:
    if await pod_member_holds(uow, pod_id, member, _SEES_CONTACTS):
        return "Their channel is in Contacts."
    return _CANNOT_OPEN
