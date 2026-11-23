"""A member's line in a channel, answered by the pod's agents.

Lemma is one more place to talk to the pod. Channels decides which agents
answer a member's line, by one rule wherever it was typed
(``ChannelMessagePosted.answering_agent_ids``: those it names, those in the
channel that answer every line, and in the member's own channel the pod's
agent); this module does the answering. A line heard on a platform is
answered at its door by the bot it came through (``agent_surfaces``); any
other agent the rule names for it starts here, and answers at its place. Each agent answers in
the member's own conversation with it at the line's place -- Lemma itself for
a line typed here, the platform place for one heard there -- with the member's
access, like any turn they start -- and its answer comes back as a line in the
thread of the line it answers, said for them.

The turn records the line it answers (``channels.contracts.answers``), and
the run that ends answers exactly those lines: a member who writes L1 and then
L2 while the agent works on L1 has the answer under L1, where they asked. What
the run is shown of the channel around the line is kept as which lines, never
what they said (``channels.contracts.backgrounds``).

In a group each thread is a place of its own (``domain/run_places``), so a line
in another thread never joins a run answering one: it is queued behind that
run, as a platform's message said elsewhere is, and the follow-up turn after
it answers the line in its own thread. A reply in the same thread joins the
run. A run that fails or is stopped says so where it was asked, and one
refused before it started says why; the answer to somebody the agent asked
for the member comes back to the member, after the line that set it asking.

Safe to run twice: the turn is started with the line as its client id, so a
retried job finds the turn it already started. The run's answer is posted by
the one path every run's is (``run_answers``).
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import select

from app.core.authorization.current import reset_current_context, set_current_context
from app.core.authorization.factory import create_authorization_data_service
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent.infrastructure.db.channel_conversations import (
    ChannelConversations,
)
from app.modules.agent.contracts.channel_replies import (
    answer_line_client_id,
)
from app.modules.agent.domain.private_notes import (
    HEARD_IN_CHANNEL,
    LEMMA_CHANNEL,
    PRIVATE_NOTE_REQUEST_KEY,
)
from app.modules.agent.domain.run_places import (
    ASKED_IN_THREAD_KEY,
    message_place,
    run_place,
    thread_of,
)
from app.modules.agent.domain.value_objects import ConversationOrigin
from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.channel_answer_lines import (
    COULD_NOT_ANSWER,
    STOPPED_BEFORE_ANSWERING,
    say_refused,
)
from app.modules.agent.services.live_conversation import CONVERSATION_STREAM
from app.modules.agent.application.conversations.start import (
    StartConversation,
    authorize_opening,
)
from app.modules.agent.application.turns.send_message import SendMessage
from app.modules.agent.domain.run_kind import RunKind
from app.modules.agent.infrastructure import repositories
from app.modules.channels.contracts import (
    ChannelKind,
    Channel,
    ChannelMessage,
    SenderKind,
    channel_by_id,
    source_by_id,
)
from app.modules.channels.contracts.answers import start_answering
from app.modules.channels.contracts.backgrounds import (
    BACKGROUND_KEY,
    BackgroundRefs,
    BackgroundView,
    refs_of,
)
from app.modules.channels.contracts.live import announce_answering
from app.modules.channels.contracts.lines import (
    line_by_id,
    member_background,
)

logger = get_logger(__name__)
#: The most lines of the channel a run answering a line is shown.
IN_APP_CONTEXT_LINES = 30


def turn_client_id(message_id: UUID) -> str:
    """The turn a line starts, named by the line: a retry is the same turn."""
    return f"channel-line:{message_id}"


def answer_client_id(run_id: UUID) -> str:
    """The line a run's answer is, named by the run: a retry is the same line."""
    return answer_line_client_id(run_id)


async def answer_line(
    uow_factory: UnitOfWorkFactory, *, message_id: UUID, agent_id: UUID
) -> UUID | None:
    """Start ``agent_id``'s turn answering a member's line; the conversation it
    answers in, or ``None`` when it does not answer it."""
    async with uow_factory() as uow:
        line = await line_by_id(uow, message_id)
        channel = await channel_by_id(uow, line.channel_id) if line else None
    if line is None or channel is None or not _answerable(line):
        return None
    async with uow_factory() as uow:
        agent_name = await _pod_agent_name(
            uow, pod_id=channel.pod_id, agent_id=agent_id
        )
        if agent_name is None:
            logger.warning(
                "agent.channel_answer.not_a_pod_agent.observed",
                pod_id=str(channel.pod_id),
                channel_id=str(channel.id),
                agent_id=str(agent_id),
            )
            return None
        return await _answer_as(
            uow, channel=channel, line=line, agent_id=agent_id, agent_name=agent_name
        )


def _answerable(line: ChannelMessage) -> bool:
    """A member's own line, still there -- written in Lemma, wherever it was
    carried, or heard on a platform. A members' note is answered too, by the
    agents it names, as a note (``channel_answer_lines``)."""
    return (
        line.sender.kind is SenderKind.MEMBER
        and line.sender.user_id is not None
        and not line.is_deleted
    )


async def _pod_agent_name(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, agent_id: UUID
) -> str | None:
    """The agent's stored name, if it is one of this pod's."""
    return await uow.session.scalar(
        select(AgentModel.name).where(
            AgentModel.id == agent_id, AgentModel.pod_id == pod_id
        )
    )


async def _answer_as(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel: Channel,
    line: ChannelMessage,
    agent_id: UUID,
    agent_name: str,
) -> UUID | None:
    """Have this agent answer a member's channel line in their conversation on
    the line's platform, as the member; the conversation answered, or None."""
    pod_id, user_id = channel.pod_id, line.sender.user_id
    assert user_id is not None
    context = await create_authorization_data_service(uow).build_user_context(
        user_id=user_id, pod_id=pod_id
    )
    token = set_current_context(context)
    try:
        # Authorized first, since that commits; then locked, looked for again,
        # and opened -- as a surface opens one.
        await authorize_opening(
            repositories.AgentRepository(uow),
            pod_id=pod_id,
            agent_name=agent_name,
            user_id=user_id,
        )
        conversation_id = await _members_conversation(
            uow,
            channel=channel,
            line=line,
            user_id=user_id,
            agent_id=agent_id,
            agent_name=agent_name,
        )
        await start_answering(
            uow,
            message_id=line.id,
            channel_id=line.channel_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
        )
        background = await member_background(uow, line=line, limit=IN_APP_CONTEXT_LINES)
        metadata = turn_metadata(channel, line, background)
        started = await SendMessage.build(uow).send(
            conversation_id=conversation_id,
            user_id=user_id,
            content=line.text,
            pod_id=pod_id,
            agent_name=agent_name,
            message_metadata=metadata,
            require_execute_grant=False,
            client_message_id=turn_client_id(line.id),
            kind=RunKind.CHANNEL,
            # Heard on a platform: answered there (``domain/run_places``).
            asked_at=line.source_id,
        )
        # The run that answers it: the one this turn started, or the one
        # already going that it joins -- not one answering another thread,
        # behind which it waits for the follow-up turn that answers it.
        joins = started.started_new_run or await _joins(
            uow, started.agent_run_id, line, metadata
        )
        if joins:
            await start_answering(
                uow,
                message_id=line.id,
                channel_id=line.channel_id,
                agent_id=agent_id,
                conversation_id=conversation_id,
                run_id=started.agent_run_id,
            )
        await uow.commit()
        announce_answering(
            pod_id=pod_id,
            channel_id=channel.id,
            message_id=line.id,
            agent_id=agent_id,
            for_user_id=user_id,
            state="started",
            conversation_id=conversation_id,
            # A line waiting its turn is not shown another thread's answer
            # as it is written.
            agent_run_id=started.agent_run_id if joins else None,
            stream=(CONVERSATION_STREAM, str(conversation_id)) if joins else None,
        )
        return conversation_id
    except DomainError as refused:
        logger.warning(
            "agent.channel_answer.refused.observed",
            pod_id=str(pod_id),
            channel_id=str(channel.id),
            agent_id=str(agent_id),
            reason=refused.code,
        )
        # Nothing of the turn stays; the member hears why, where they asked.
        await uow.rollback()
        await say_refused(uow, line=line, agent_id=agent_id, refused=refused)
        await uow.commit()
        return None
    finally:
        reset_current_context(token)


async def _joins(
    uow: SqlAlchemyUnitOfWork,
    run_id: UUID,
    line: ChannelMessage,
    metadata: dict[str, object],
) -> bool:
    """Whether a line sent while ``run_id`` works joins it: said at the run's
    own place -- the source a heard line came from, the same group thread, or
    anywhere in a member's own channel -- which is what steering takes in
    (``domain/run_places``)."""
    run = await Runs(uow).get_agent_run(run_id)
    if run is None:
        return False
    source = metadata.get("source")
    place = message_place(
        line.source_id,
        source=source if isinstance(source, str) else None,
        thread=thread_of(metadata),
    )
    return place is None or place == run_place(run.state.privacy)


async def _members_conversation(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel: Channel,
    line: ChannelMessage,
    user_id: UUID,
    agent_id: UUID,
    agent_name: str,
) -> UUID:
    """The member's conversation with the agent on the line's platform: the
    one a heard line came from, or Lemma itself for one typed here -- never
    the one they keep with it on WhatsApp or by email."""
    platform = await _heard_on(uow, line)
    seats = ChannelConversations(uow)
    await seats.lock_person(
        channel_id=channel.id,
        pod_id=channel.pod_id,
        agent_id=agent_id,
        person_id=user_id,
        platform=platform,
    )
    found = await seats.in_channel(
        channel_id=channel.id,
        pod_id=channel.pod_id,
        agent_id=agent_id,
        person_id=user_id,
        platform=platform,
    )
    if found is not None:
        return found
    opened = await StartConversation.build(uow).start(
        pod_id=channel.pod_id,
        agent_name=agent_name,
        user_id=user_id,
        title=(channel.title or "Lemma")[:255],
        metadata={"channel_kind": channel.kind.value},
        origin=ConversationOrigin.CHANNEL,
        require_execute_grant=False,
        channel_id=channel.id,
        platform=platform,
    )
    return opened.id


async def _heard_on(uow: SqlAlchemyUnitOfWork, line: ChannelMessage) -> str | None:
    """The platform a line was heard on; None for one typed in Lemma."""
    if not line.heard_outside or line.source_id is None:
        return None
    source = await source_by_id(uow, line.source_id)
    return source.platform if source is not None else None


def turn_metadata(
    channel: Channel, line: ChannelMessage, background: Sequence[ChannelMessage]
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "channel_id": str(channel.id),
        "channel_message_id": str(line.id),
    }
    if line.heard_outside:
        # Read back as the line heard at its place (``run_answers``), which is
        # its source -- never a thread in Lemma.
        metadata["source"] = HEARD_IN_CHANNEL
        if line.external_message_id:
            metadata["external_message_id"] = line.external_message_id
    else:
        metadata["source"] = LEMMA_CHANNEL
        if channel.kind is ChannelKind.GROUP:
            # Each thread of a group is a place of its own: a line in another
            # thread waits for a turn of its own (``domain/run_places``). The
            # server names the place from it when the message is stored.
            metadata[ASKED_IN_THREAD_KEY] = str(line.thread_root_id or line.id)
    if line.members_only:
        # A note among members: its answer is one too, and nothing said for
        # it is repeated anywhere it would be carried.
        metadata[PRIVATE_NOTE_REQUEST_KEY] = True
    if channel.title:
        metadata["channel_name"] = channel.title
    if line.sender.display_name:
        metadata["sender_display_name"] = line.sender.display_name
    if background and line.sender.user_id is not None:
        # Which lines, not what they said: read from the timeline whenever the
        # prompt is built, as this member may read them then.
        metadata[BACKGROUND_KEY] = BackgroundRefs(
            pod_id=channel.pod_id,
            channel_id=channel.id,
            # Heard in a group: what a member's run there is shown, answered
            # where everybody in it reads the answer.
            view=BackgroundView.MEMBER_IN_GROUP
            if line.heard_outside
            else BackgroundView.MEMBER_IN_LEMMA,
            user_id=line.sender.user_id,
            items=refs_of(background),
        ).model_dump(mode="json")
    return metadata


__all__ = [
    "COULD_NOT_ANSWER",
    "IN_APP_CONTEXT_LINES",
    "STOPPED_BEFORE_ANSWERING",
    "answer_client_id",
    "answer_line",
    "turn_client_id",
    "turn_metadata",
]
