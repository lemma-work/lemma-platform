"""What a chat channel does with the agent's conversations.

Published for `agent_surfaces`, and for the modules that share its threads
(`channels`, `web_login`). A conversation is one agent working for one person
in a channel, and a surface hearing a message there:

* **opens or continues** the person's conversation: finds the one they have
  with the agent in the channel under a lock on the person, or opens one and
  places it there;
* **delivers** what was said: a turn that runs the agent, or a line in no run
  -- a notification, a delivery notice, a member's follow-up;
* **holds for a person** what somebody outside the pod wrote while nobody may
  answer them yet;
* **reads the reply** a finished run left, and what a contact saw
  (``contracts/channel_replies``);
* **reads the pending pauses** -- a question, an approval, a sign-in -- and
  answers them, typed or tapped, or retries a failed turn;
* **folds contacts**: hands a contact's conversation to another member, names
  the contact on a visitor's chat, moves a merged channel's conversations.

Every operation takes the unit of work, so a request's transaction and a short
one around long I/O are the same call. Each runs a use case or a repository;
none runs SQL here (``tests/unit/test_contracts_run_no_sql``).

The agent's words a surface hands on -- what a typed reply means, a progress
tool's arguments, a sign-in link -- are ``contracts/channel_words``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.application.conversations.read import ConversationReads
from app.modules.agent.application.conversations.start import (
    StartConversation,
    authorize_opening,
)
from app.modules.agent.application.pauses.resolve import ResolvePause
from app.modules.agent.application.turns.retry import RetryFailedRun
from app.modules.agent.application.turns.send_message import SendMessage
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.domain.approvals import ApprovalResolution
from app.modules.agent.domain.conversations import surface_origin
from app.modules.agent.domain.errors import ApprovalNotOwnedError
from app.modules.agent.domain.outsiders import Audience, AudienceKind
from app.modules.agent.domain.pauses import APPROVAL_KINDS, PERSON_KINDS, Pause
from app.modules.agent.domain.private_notes import answers_lemma_message
from app.modules.agent.domain.value_objects import AgentRunApprovalDecision
from app.modules.agent.infrastructure.db.channel_conversations import (
    ChannelConversations,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.repositories import AgentRepository


@dataclass(frozen=True, slots=True)
class SurfaceConversation:
    """A conversation as a surface needs it: who owns it, and how fresh it is.

    Scalars rather than the entity, so no surface starts reading a field
    nobody chose to publish.
    """

    id: UUID
    user_id: UUID
    pod_id: UUID
    #: Absent for the pod's own assistant on rows written before it had one.
    agent_id: UUID | None
    title: str | None
    #: Last write to the row. A conversation has no "last spoken at"; this is
    #: the closest thing, and it is what decides whether a notification
    #: continues the thread or opens a new one.
    updated_at: datetime
    #: Whom routing opened it to answer: a member, a group's people from
    #: outside the pod, or one contact in a private chat (``domain/outsiders``).
    audience: Audience = Audience()


@dataclass(frozen=True, slots=True)
class PendingInteraction:
    """A pause awaiting a person, as a surface renders it."""

    tool_call_id: str
    #: The name the call is stored under: ``"ask_user"``, ``"request_approval"``.
    kind: str
    #: The tool call's own arguments, as stored. Unvalidated on purpose: the
    #: surface validates them against `AskUserRequest` and must be able to tell
    #: an unparseable payload from an absent one.
    tool_args: dict[str, object]
    agent_run_id: UUID | None
    #: Rendered as a decision, not a question.
    is_approval: bool = False


@dataclass(frozen=True, slots=True)
class SurfaceAgentIdentity:
    """An agent as a message header names it. ``is_pod_default`` rather than
    the stored ``pod_default`` name, so each surface picks its own label."""

    id: UUID
    name: str
    is_pod_default: bool
    icon_url: str | None


def _pending(pause: Pause | None) -> PendingInteraction | None:
    if pause is None:
        return None
    return PendingInteraction(
        tool_call_id=pause.tool_call_id,
        kind=pause.tool_name,
        tool_args=pause.call_args,
        agent_run_id=pause.run_id,
        is_approval=pause.kind in APPROVAL_KINDS,
    )


def _conversation(entity) -> SurfaceConversation:
    return SurfaceConversation(
        id=entity.id,
        user_id=entity.user_id,
        pod_id=entity.pod_id,
        agent_id=entity.agent_id,
        title=entity.title,
        updated_at=entity.updated_at,
        audience=Audience.of(entity),
    )


async def surface_conversation(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> SurfaceConversation | None:
    """This conversation as a surface sees it, or ``None`` once it is gone --
    an ordinary outcome for a thread somebody left open on a phone."""
    entity = await Conversations(uow).get_conversation(conversation_id)
    return None if entity is None else _conversation(entity)


async def lemma_message_run_started_at(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> datetime | None:
    """When the latest run started, if it answers a message typed in Lemma.

    Such a run answers a message the group never saw, so what it posts there
    is not threaded under whatever somebody in the chat last said; the time
    tells it from a run somebody in the chat has since written into.
    """
    run = await Runs(uow).get_latest_agent_run_for_conversation(conversation_id)
    if run is None or not answers_lemma_message(run.state):
        return None
    return run.started_at


async def authorize_surface_conversation(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, agent_name: str | None, user_id: UUID
) -> None:
    """Refuse a person who may not run this agent, as opening would. It
    releases the pooled connection, so a caller opening under a lock checks
    here first and opens with ``require_execute_grant=False``."""
    await authorize_opening(
        AgentRepository(uow), pod_id=pod_id, agent_name=agent_name, user_id=user_id
    )


async def open_surface_conversation(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    agent_name: str | None,
    user_id: UUID,
    title: str | None,
    metadata: dict[str, object] | None = None,
    require_execute_grant: bool = True,
    contact_id: UUID | None = None,
    channel_id: UUID | None = None,
    for_notification: bool = False,
    started_on: str | None = None,
) -> SurfaceConversation:
    """Start the conversation behind a surface thread.

    ``for_notification`` opens a thread the recipient owns, unchecked for
    permission to run the agent writing to them. ``contact_id`` opens a
    contact's conversation, kept by the member ``user_id``, every run in it
    authorizing as that contact (``domain/outsiders``). ``started_on`` is the
    platform it is counted under. The caller sets the authorization context.
    """
    conversation = await StartConversation.build(uow).start(
        pod_id=pod_id,
        agent_name=agent_name,
        user_id=user_id,
        title=title,
        metadata=metadata,
        require_execute_grant=require_execute_grant,
        contact_id=contact_id,
        channel_id=channel_id,
        origin=surface_origin(contact_id=contact_id, for_notification=for_notification),
        started_on=started_on,
    )
    return _conversation(conversation)


async def start_surface_turn(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID | None,
    user_id: UUID,
    pod_id: UUID,
    content: str,
    agent_name: str | None = None,
    message_metadata: dict[str, object] | None = None,
) -> UUID | None:
    """Record an inbound message and start the run that answers it; ``None``
    when the message joined a run already going."""
    result = await SendMessage.build(uow).send(
        conversation_id=conversation_id,
        user_id=user_id,
        content=content,
        pod_id=pod_id,
        agent_name=agent_name,
        message_metadata=message_metadata,
    )
    return result.agent_run_id if result.started_new_run else None


async def append_notification_message(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    message: str,
    notification_id: UUID,
) -> None:
    """Write an outbound notification into the recipient's thread, once."""
    from app.modules.agent.application.channels.record import RecordInConversation

    await RecordInConversation.build(uow).notification(
        conversation_id=conversation_id,
        message=message,
        notification_id=notification_id,
    )


async def append_delivery_notice(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    message: str,
    metadata: dict[str, str],
) -> None:
    """Write a line about a delivery into the conversation, in no run: what
    the person reading in Lemma should know of a message sent on their behalf
    elsewhere -- that it may not have arrived."""
    from app.modules.agent.application.channels.record import RecordInConversation

    await RecordInConversation.build(uow).delivery_notice(
        conversation_id=conversation_id, message=message, metadata=metadata
    )


async def pending_interaction(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> PendingInteraction | None:
    """The oldest unresolved pause of any kind, or ``None``: what a typed
    reply answers. Not what a card renders (:func:`pending_approval`)."""
    return _pending(
        await ConversationReads.build(uow).pending_user_interaction(
            conversation_id=conversation_id
        )
    )


async def undecided_pauses(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> list[str]:
    """Every pause nobody has decided yet, by tool call id, oldest first:
    what a new turn denies on the person's behalf."""
    pending = await Pauses(uow).pending(conversation_id, kinds=PERSON_KINDS)
    return [pause.tool_call_id for pause in pending]


async def pending_question(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> PendingInteraction | None:
    """The oldest unresolved ``ask_user``, or ``None``."""
    return _pending(
        await ConversationReads.build(uow).pending_ask_user(
            conversation_id=conversation_id
        )
    )


async def pending_approval(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> PendingInteraction | None:
    """The oldest unresolved ``request_approval``, or ``None`` -- its own
    lookup, because an older, never-tapped ``ask_user`` would hide it."""
    return _pending(
        await ConversationReads.build(uow).pending_approval(
            conversation_id=conversation_id
        )
    )


async def pending_sign_in(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> PendingInteraction | None:
    """The oldest unresolved ``browser_sign_in``, or ``None``: its arguments
    carry the origin and reason, and its being open is "still waiting"."""
    return _pending(
        await ConversationReads.build(uow).pending_sign_in(
            conversation_id=conversation_id
        )
    )


async def resolve_pending_interaction(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    approval_id: str,
    user_id: UUID,
    pod_id: UUID | None = None,
    decision: AgentRunApprovalDecision,
    response: dict[str, object] | None = None,
) -> ApprovalResolution | None:
    """Record a person's decision and resume the run it paused.

    ``None`` when the conversation is gone and nothing was recorded. Otherwise
    the resolution, whose ``decision`` is the one that *stands*: a pause the
    person moved on from was already denied, and a tap adopts that denial.
    ``status`` is ``"queued"`` when a worker owns the rest. Skips the HTTP
    entry point's authorization: the surface matched the person against the
    owner, under a webhook deadline. ``pod_id`` defaults to the
    conversation's.
    """
    entity = await Conversations(uow).get_conversation(conversation_id)
    if entity is None:
        return None
    return await ResolvePause.build(uow).resolve(
        conversation=entity,
        approval_id=approval_id,
        user_id=user_id,
        pod_id=pod_id if pod_id is not None else entity.pod_id,
        decision=decision,
        response=response or {},
    )


async def retry_failed_run(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    user_id: UUID,
    pod_id: UUID,
    agent_name: str | None = None,
) -> None:
    """Run the conversation's last failed turn again, as its owner.

    Raises rather than reporting: a tapped Retry and a ``/retry`` command want
    different words for a failure, and only they know which.
    """
    auth_ctx = await create_authorization_data_service(uow).build_user_context(
        user_id=user_id, pod_id=pod_id
    )
    token = set_current_context(auth_ctx)
    try:
        await RetryFailedRun.build(uow).retry(
            conversation_id=conversation_id,
            user_id=user_id,
            pod_id=pod_id,
            agent_name=agent_name,
        )
    finally:
        reset_current_context(token)


async def surface_agent_identity(
    uow: SqlAlchemyUnitOfWork, agent_id: UUID
) -> SurfaceAgentIdentity | None:
    """How to name this agent in front of a person, or ``None`` if it is gone."""
    header = await AgentRepository(uow).header(agent_id)
    if header is None:
        return None
    identifier, name, kind, icon_url = header
    return SurfaceAgentIdentity(
        id=identifier,
        name=name,
        is_pod_default=kind is AgentKind.POD_DEFAULT,
        icon_url=icon_url,
    )


# -- the person's conversation in a channel -----------------------------------


async def conversation_in_channel(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    user_id: UUID,
    contact_id: UUID | None,
) -> UUID | None:
    """The person's live conversation with this agent in the channel, if any.

    The person is the contact when ``contact_id`` is set -- whoever looks after
    them -- and otherwise the member ``user_id`` names. An archived
    conversation, or a sub-agent's, is never continued.
    """
    return await ChannelConversations(uow).in_channel(
        channel_id=channel_id,
        pod_id=pod_id,
        agent_id=agent_id,
        person_id=contact_id if contact_id is not None else user_id,
    )


async def lock_person_in_channel(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel_id: UUID,
    pod_id: UUID,
    agent_id: UUID | None,
    person_id: UUID,
) -> None:
    """Serialise finding-or-opening one person's conversation in a channel
    until this transaction ends: two first messages open one. The unique index
    behind it is the backstop, not the plan."""
    await ChannelConversations(uow).lock_person(
        channel_id=channel_id, pod_id=pod_id, agent_id=agent_id, person_id=person_id
    )


async def place_in_channel(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, channel_id: UUID
) -> None:
    """Put a conversation that has no channel yet in this one."""
    await ChannelConversations(uow).place(
        conversation_id=conversation_id, channel_id=channel_id
    )


async def channel_of_conversation(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID
) -> UUID | None:
    """The channel a conversation works in, or ``None``."""
    return await ChannelConversations(uow).channel_of(conversation_id)


# -- contacts ------------------------------------------------------------------


async def name_contact_on_conversation(
    uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID, contact_id: UUID
) -> bool:
    """Make a web visitor's chat that answers nobody yet this contact's.

    A member's conversation never becomes a contact's: only one with no
    contact changes, which the caller vouches for by holding the visitor's
    session. Returns whether it changed.
    """
    return await ChannelConversations(uow).name_contact(
        conversation_id=conversation_id, contact_id=contact_id
    )


async def move_contact_conversation(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    contact_id: UUID,
    to_user_id: UUID,
) -> bool:
    """Give this contact's conversation -- the pod's history with them -- to
    the member now looking after contacts. Only a conversation marked as this
    contact's moves. Returns whether it is now ``to_user_id``'s."""
    return await ChannelConversations(uow).give_to_member(
        conversation_id=conversation_id, contact_id=contact_id, to_user_id=to_user_id
    )


async def move_channel_conversations(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    from_channel_id: UUID,
    to_channel_id: UUID,
) -> int:
    """A channel folded into another: its conversations work in that one now,
    those whose person is seated there already folded into that conversation
    (``application/channels/fold``). Returns how many moved."""
    from app.modules.agent.application.channels.fold import FoldConversations

    return await FoldConversations.build(uow).move_channel(
        pod_id=pod_id, from_channel_id=from_channel_id, to_channel_id=to_channel_id
    )


async def hold_outside_turn(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    text: str,
    metadata: dict[str, object],
    reply: str | None,
) -> None:
    """Keep what somebody outside the pod wrote, and start no run, so the
    member answering it reads what was asked (``RecordInConversation``)."""
    from app.modules.agent.application.channels.record import RecordInConversation

    await RecordInConversation.build(uow).held_turn(
        conversation_id=conversation_id, text=text, metadata=metadata, reply=reply
    )


async def append_follow_up(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    line_id: UUID,
    sent_by_user_id: UUID,
    delivered: bool | None = None,
) -> None:
    """Mark where a member's line to a contact falls in their conversation,
    once per line (``RecordInConversation``)."""
    from app.modules.agent.application.channels.record import RecordInConversation

    await RecordInConversation.build(uow).follow_up(
        conversation_id=conversation_id,
        line_id=line_id,
        sent_by_user_id=sent_by_user_id,
        delivered=delivered,
    )


__all__ = [
    "AgentRunApprovalDecision",
    "ApprovalNotOwnedError",
    "ApprovalResolution",
    "Audience",
    "AudienceKind",
    "PendingInteraction",
    "SurfaceAgentIdentity",
    "SurfaceConversation",
    "append_delivery_notice",
    "append_follow_up",
    "append_notification_message",
    "authorize_surface_conversation",
    "channel_of_conversation",
    "conversation_in_channel",
    "hold_outside_turn",
    "lemma_message_run_started_at",
    "lock_person_in_channel",
    "move_channel_conversations",
    "move_contact_conversation",
    "name_contact_on_conversation",
    "open_surface_conversation",
    "pending_approval",
    "pending_interaction",
    "pending_question",
    "pending_sign_in",
    "place_in_channel",
    "resolve_pending_interaction",
    "retry_failed_run",
    "start_surface_turn",
    "surface_agent_identity",
    "surface_conversation",
    "undecided_pauses",
]
