"""The agent's line answering a member's line in Lemma -- or saying why not.

Whatever a turn on a line ends with is said as the agent's line for the
member, after the line it answers (``channel_answers``): the run's answer;
the word that the run failed, or was stopped, before it answered; or why the
turn was refused before it started. Without the last two the member read
the agent start, and then nothing.

A members' note is answered as a note: carried nowhere, and its answer may
come from a private run (a note's run is one, ``domain/private_notes``).
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select

from app.core.authorization.delegation import effective_agent_id, is_pod_default_agent
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.redaction import redact_text
from app.core.text.thinking_tags import strip_context_echo, strip_thinking_tokens
from app.modules.agent.contracts.run_failures import user_safe_stored_reason
from app.modules.agent.contracts.channel_replies import (
    RunReplyMaterial,
    answer_line_client_id,
)
from app.modules.agent.domain.asked_questions import line_client_id
from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.channels.contracts import (
    Channel,
    ChannelKind,
    ChannelMessage,
    NewLine,
    Principal,
    Sender,
    channel_by_id,
    post,
)
from app.modules.channels.contracts.places import places_of

#: What a line says when its run failed and left no reason of its own.
COULD_NOT_ANSWER = "I couldn't answer this: something went wrong on my side."
#: What a line says when its run was stopped before it answered.
STOPPED_BEFORE_ANSWERING = "I was stopped before I finished answering this."
#: The most of a refusal's or a failure's reason a line repeats.
MAX_REASON_CHARS = 300


async def post_answer(
    uow: SqlAlchemyUnitOfWork,
    *,
    run_id: UUID,
    conversation: ConversationModel,
    line: ChannelMessage,
    material: RunReplyMaterial | None,
) -> ChannelMessage | None:
    """The run's answer to ``line``; nothing for a run that wrote none."""
    if material is None or not material.answer:
        return None
    if material.private and not line.members_only:
        return None
    # The same boundary as a platform's answer: no reasoning, and none of the
    # platform's own context blocks a model repeated back.
    text = strip_context_echo(strip_thinking_tokens(material.answer))
    if not text:
        return None
    return await _post_line(
        uow,
        line=line,
        agent_id=effective_agent_id(conversation.agent_id, pod_id=conversation.pod_id),
        for_user_id=conversation.user_id,
        text=text,
        client_message_id=answer_line_client_id(run_id),
        conversation_id=conversation.id,
        run_id=run_id,
    )


async def post_why_unanswered(
    uow: SqlAlchemyUnitOfWork,
    *,
    run_id: UUID,
    conversation: ConversationModel,
    line: ChannelMessage,
    material: RunReplyMaterial | None,
) -> ChannelMessage | None:
    """A run that failed, or was stopped, says so where its answer would be.
    In Lemma only: a group's place is not told about a run nobody there saw."""
    if material is None:
        return None
    if material.status == "FAILED":
        reason = clipped(user_safe_stored_reason(material.error))
        text = f"I couldn't answer this: {reason}" if reason else COULD_NOT_ANSWER
    elif material.status == "STOPPED":
        text = STOPPED_BEFORE_ANSWERING
    else:
        return None
    return await _post_line(
        uow,
        line=line,
        agent_id=effective_agent_id(conversation.agent_id, pod_id=conversation.pod_id),
        for_user_id=conversation.user_id,
        text=text,
        client_message_id=answer_line_client_id(run_id),
        conversation_id=conversation.id,
        run_id=run_id,
        carried=False,
    )


async def say_refused(
    uow: SqlAlchemyUnitOfWork,
    *,
    line: ChannelMessage,
    agent_id: UUID,
    refused: DomainError,
) -> ChannelMessage | None:
    """The agent's line saying it will not answer, and the reason it was
    given -- an error's own sentence, written for whoever reads it."""
    reason = clipped(refused.message)
    assert line.sender.user_id is not None  # an answerable line's
    return await _post_line(
        uow,
        line=line,
        agent_id=agent_id,
        for_user_id=line.sender.user_id,
        text=f"I can't answer this: {reason}" if reason else COULD_NOT_ANSWER,
        client_message_id=line_client_id(f"refused:{line.id}:{agent_id}"),
        carried=False,
    )


def clipped(reason: str | None) -> str:
    """A reason as a line repeats it: one line, any secret masked, short."""
    clean = " ".join(redact_text(reason or "").split())
    if len(clean) > MAX_REASON_CHARS:
        clean = clean[: MAX_REASON_CHARS - 1].rstrip() + "…"
    return clean


async def _post_line(
    uow: SqlAlchemyUnitOfWork,
    *,
    line: ChannelMessage,
    agent_id: UUID,
    for_user_id: UUID,
    text: str,
    client_message_id: str,
    conversation_id: UUID | None = None,
    run_id: UUID | None = None,
    carried: bool = True,
) -> ChannelMessage | None:
    """The agent's line answering ``line``, said for the member, where an
    answer to it goes. ``carried``: it follows the line to a group's place,
    as an answer does. A note's answer is a note, carried nowhere."""
    agent_name = (
        None  # the pod's own assistant is named by the channel, as the pod
        if is_pod_default_agent(agent_id, pod_id=line.pod_id)
        else await uow.session.scalar(
            select(AgentModel.name).where(AgentModel.id == agent_id)
        )
    )
    channel = await channel_by_id(uow, line.channel_id)
    in_thread, place = where_the_answer_goes(
        channel, line, place_threads=await _place_threads(uow, line)
    )
    if not carried or line.members_only:
        place = None
    return await post(
        uow,
        channel_id=line.channel_id,
        line=NewLine(
            sender=Sender.agent(
                agent_id,
                on_behalf_of=Principal.member(for_user_id),
                display_name=agent_name,
            ),
            text=text,
            target_source_id=place,
            members_only=line.members_only,
            client_message_id=client_message_id,
            conversation_id=conversation_id,
            agent_run_id=run_id,
            thread_root_id=in_thread,
            reply_to_id=line.id,
        ),
        deliver_to=[place] if place else [],
    )


#: The platforms whose channels thread a bot's answer under the message it
#: answers. Elsewhere -- WhatsApp, Telegram, a chat made in Lemma -- an answer
#: is a message of the chat like any other, aimed at what it answers.
THREADED_PLATFORMS = frozenset({"SLACK", "TEAMS"})


async def _place_threads(uow: SqlAlchemyUnitOfWork, line: ChannelMessage) -> bool:
    """Whether the place ``line`` is at threads a bot's answers there."""
    if line.place_id is None:
        return False
    for source in await places_of(uow, line.channel_id):
        if source.id == line.place_id:
            return source.platform.upper() in THREADED_PLATFORMS
    return False


def where_the_answer_goes(
    channel: Channel | None, line: ChannelMessage, *, place_threads: bool = False
) -> tuple[UUID | None, UUID | None]:
    """The thread an answer is a reply in, and the place it is carried to.

    A member's own channel and a group read as a chat: an answer to a line
    in the main timeline is in the main timeline too, right after it and
    pointing at it (``reply_to_id``), and is carried to the group's place as
    a reply to that message there. A group mirroring a platform that threads a
    bot's answers (``place_threads``: Slack, Teams) keeps its habit: the
    answer is a reply in the line's thread, there and here. A line already in
    a thread is answered in that thread. A contact's channel answers a
    member's line in its thread, off the contact's own line of talk, and
    never carries it: the answer is the member's, made with their access,
    not the contact's to read.
    """
    kind = channel.kind if channel is not None else None
    if line.thread_root_id is not None:
        in_thread: UUID | None = line.thread_root_id
    elif kind is ChannelKind.DIRECT:
        in_thread = None
    elif kind is ChannelKind.GROUP:
        in_thread = line.id if place_threads else None
    else:
        in_thread = line.id
    return in_thread, line.place_id if kind is ChannelKind.GROUP else None


__all__ = [
    "COULD_NOT_ANSWER",
    "STOPPED_BEFORE_ANSWERING",
    "THREADED_PLATFORMS",
    "clipped",
    "post_answer",
    "post_why_unanswered",
    "say_refused",
    "where_the_answer_goes",
]
