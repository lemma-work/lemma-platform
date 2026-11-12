"""What a surface heard, as lines on the channel it happened in.

Every message a person sends the pod is a line of the channel its place
belongs to (``app.modules.channels``): the timeline the pod's members read,
and the background a later run is handed. Written as the message enters the
person's conversation. The agent's answer is a line too, posted before it is
sent and carried by its delivery (``run_reply_lines``).

A group's messages are written earlier, by ``group_log``, as they arrive:
most of a group is not said to the bot, and all of it is what "what did we
decide yesterday?" asks about. The turn that takes one up adds to that line
and writes no other. The same platform message is one line however many
times it arrives.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol
from uuid import UUID

from app.core.authorization.delegation import effective_agent_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import channels as agent_channels
from app.modules.agent.contracts.channels import Audience
from app.modules.agent_surfaces.domain.entities import ParsedInboundSurfaceEvent
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.services.line_markup import Bot, LineMarkup
from app.modules.channels.contracts.deliveries import deliveries_of, delivery_sent
from app.modules.channels.contracts.placement import heard_thread_root
from app.modules.channels.contracts import (
    AccountKey,
    ChannelMessage,
    ContactsHome,
    NewLine,
    Place,
    Principal,
    Sender,
    Source,
    SourceKind,
    answered_in,
    post,
    source_by_id,
    source_for,
)


#: A web chat's platform, as its channel source names it.
WEB = "WEB"


#: What of an event's metadata answering it in place can need: how its
#: platform threads and routes a reply. Everything else there -- a subject,
#: attachments, names, quoted text, a shared contact -- is what was said, which
#: the line itself keeps.
REPLY_METADATA = frozenset(
    {
        "platform",
        # Slack and Teams: where the place is, and whether this is a thread.
        "event_type",
        "is_thread_reply",
        "channel_type",
        "channel_membership_type",
        "place_privacy",
        "is_ext_shared_channel",
        "assistant_thread_present",
        "assistant_thread_action_token",
        "team_id",
        "team_aad_group_id",
        "channel_id",
        "service_url",
        "conversation_id",
        "reply_to_id",
        # Telegram: the chat and its topic.
        "chat_id",
        "chat_type",
        "message_thread_id",
        "is_topic_message",
        # WhatsApp: the number it came to, and the group.
        "waba_id",
        "phone_number_id",
        "group_id",
        # Email: the inbox and the thread.
        "surface_address",
        "mailbox_email",
        "thread_id",
        "message_id",
        "in_reply_to",
        "references",
    }
)


def reply_ref_of(event: ParsedInboundSurfaceEvent) -> dict[str, object]:
    """What answers a heard line in place -- its place, its thread, the message
    replied to, who to answer -- and nothing of what was said: no words, no
    sender's address, number or name, none of the payload. Kept on every heard
    line, so it holds the reply target alone; still an event a reply reads
    (``ParsedInboundSurfaceEvent``), with its words empty."""
    target = event.model_copy(
        update={
            "message_text": "",
            "sender_email": None,
            "sender_phone": None,
            "sender_display_name": None,
            "sender_authentication": None,
            "metadata": {
                key: value
                for key, value in event.metadata.items()
                if key in REPLY_METADATA
            },
        }
    )
    return target.model_dump(mode="json", exclude={"raw_payload", "unasked"})


def web_place(*, widget_id: UUID, session_id: UUID) -> Place:
    """A visitor's chat on one widget: keyed by the widget and their session.
    The widget is its own account; no surface carries it."""
    return Place(
        surface_id=None,
        platform=WEB,
        account=AccountKey(workspace=f"widget:{widget_id}"),
        kind=SourceKind.PERSON,
        external_channel_id=str(widget_id),
        external_user_id=str(session_id),
    )


class ChannelLines(Protocol):
    """Where a surface writes what it heard, as channel lines."""

    async def heard(
        self, context: SurfaceChatContext, *, text: str
    ) -> ChannelMessage | None: ...


class ChannelLinesInDatabase:
    """The lines, on the channels' timelines."""

    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.uow = uow

    async def heard(
        self, context: SurfaceChatContext, *, text: str
    ) -> ChannelMessage | None:
        return await note_heard(self.uow, context, text=text)


#: The lines over one unit of work, for a writer that opens its own scopes.
type ChannelLinesFor = Callable[[SqlAlchemyUnitOfWork], ChannelLines]


async def note_heard(
    uow: SqlAlchemyUnitOfWork, context: SurfaceChatContext, *, text: str
) -> ChannelMessage | None:
    """The message this run answers, as its sender's line in the channel --
    the line, or None where there is no place to write it.

    Written once. A group's message is a line from the moment it arrived
    (``group_log``): the turn only says which conversation answers it, and who
    said it where the log could not name them (``answered_in``). Anything the
    log did not write -- a person's own chat, a voice note with no words until
    the turn hears them, a group's first message before the pod knew the
    group -- is written here.

    ``text`` is what the conversation is given -- a voice note's words, not
    the empty body it arrived with.
    """
    if context.source_id is None:
        return None
    source = await source_by_id(uow, context.source_id)
    if source is None:
        return None
    name = context.message_metadata.sender_display_name
    contact_id = context.audience.contact_id
    sender = (
        Sender.contact(contact_id, display_name=name)
        if contact_id is not None
        else Sender.member(context.message_user_id, display_name=name)
    )
    external_message_id = context.message_external_message_id
    if source.kind is SourceKind.CHAT and external_message_id is not None:
        taken_up = await answered_in(
            uow,
            channel_id=source.channel_id,
            source_id=source.id,
            external_message_id=external_message_id,
            conversation_id=context.conversation_id,
            sender=sender,
        )
        if taken_up is not None:
            return taken_up
    event = context.event
    said = await LineMarkup(uow).read(
        event,
        pod_id=source.pod_id,
        channel_id=source.channel_id,
        text=text,
        bot=Bot(surface_id=context.surface_id, name=context.agent_display_name),
    )
    return await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=sender,
            text=said.text,
            parts=said.parts,
            source_id=source.id,
            conversation_id=context.conversation_id,
            external_message_id=external_message_id,
            external_thread_id=event.external_thread_id,
            # A Slack or Teams thread is a thread here too.
            thread_root_id=await heard_thread_root(
                uow,
                source,
                external_thread_id=event.external_thread_id,
                external_message_id=external_message_id,
            ),
            # What answers this line in place: its thread, the message replied to.
            reply_ref=reply_ref_of(event),
        ),
        mentions=said.mentions,
        mentioned_agents=said.agents,
        # Written by the turn answering it: put to that conversation's agent.
        door_agent_id=await _door_agent(uow, context),
    )


async def _door_agent(
    uow: SqlAlchemyUnitOfWork, context: SurfaceChatContext
) -> UUID | None:
    """The agent this turn's message was put to: its conversation's, which
    answers it here. Any other agent the line names, or that answers every
    line in the channel, is started by ``channels`` -- the one rule."""
    conversation = await agent_channels.surface_conversation(
        uow, context.conversation_id
    )
    if conversation is None:
        return None
    return effective_agent_id(conversation.agent_id, pod_id=conversation.pod_id)


async def note_heard_on_web(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    widget_id: UUID,
    session_id: UUID,
    contact_id: UUID,
    conversation_id: UUID,
    text: str,
    display_name: str | None,
) -> None:
    """A visitor's message, as their line in their contact channel.

    The web chat is one more source of that channel, and the visitor's
    conversation is placed there with it.
    """
    source = await source_for(
        uow,
        pod_id=pod_id,
        place=web_place(widget_id=widget_id, session_id=session_id),
        home=ContactsHome(contact_id=contact_id),
    )
    await agent_channels.place_in_channel(
        uow,
        conversation_id=conversation_id,
        channel_id=source.channel_id,
        platform=source.platform,
    )
    await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=Sender.contact(contact_id, display_name=display_name),
            text=text,
            source_id=source.id,
            conversation_id=conversation_id,
        ),
    )


def answered_for(audience: Audience, user_id: UUID) -> Principal:
    """Whom an answer was for, and so whose access it was made with.

    A contact's conversation answers the contact, from what the pod made
    Public; any other answers the member whose conversation it is, with their
    own access -- the one member a group's page shows its words to.
    """
    if audience.contact_id is not None:
        return Principal.contact(audience.contact_id)
    return Principal.member(user_id)


async def shown_on_web(
    uow: SqlAlchemyUnitOfWork, line: ChannelMessage, source: Source
) -> None:
    """A line for a web chat, which shows it from the conversation: its
    delivery there is ``SENT`` at once, and nothing is sent. Every line for a
    place has its delivery row, so the line says where it went the same way
    whatever the platform."""
    for delivery in await deliveries_of(uow, [line.id]):
        if delivery.source_id == source.id:
            await delivery_sent(
                uow, delivery.id, platform=source.platform, external_message_id=None
            )
