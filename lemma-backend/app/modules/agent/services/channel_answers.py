"""A member writing in Lemma's own channels, answered by the pod's agents.

Lemma is one more place to talk to the pod. Channels decides which agents
answer a member's line (``ChannelMessagePosted.answering_agent_ids``: those it
names, those in the channel that answer every line, and in the member's own
channel the pod's agent); this module does the answering. Each agent answers in
the member's own conversation with it in that channel -- with the member's
access, like any turn they start -- and its answer comes back as a line in the
thread of the line it answers, said for them.

The turn records the line it answers (``channels.contracts.answers``), and
the run that ends answers exactly those lines: a member who writes L1 and then
L2 while the agent works on L1 has the answer under L1, where they asked. What
the run is shown of the channel around the line is kept as which lines, never
what they said (``channels.contracts.backgrounds``).

In a group each line is a thread of its own, so a line in another thread
never joins a run answering one: it waits for that run to end and is answered
by its own (``LineWaits``). A run that fails or is stopped says so where it
was asked, and one refused before it started says why; the answer to
somebody the agent asked for the member comes back to the member, after the
line that set it asking.

Both halves are safe to run twice. The turn is started with the line as its
client id, so a retried job finds the turn it already started; the answer is
posted with the run as its client id, so a retried post is the same line.
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
    run_reply_material,
)
from app.modules.agent.domain.pod_asks import HANDED_BACK_SOURCES
from app.modules.agent.domain.private_notes import (
    LEMMA_CHANNEL,
    PRIVATE_NOTE_REQUEST_KEY,
)
from app.modules.agent.domain.value_objects import ConversationOrigin, QueueState
from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent.infrastructure.models.conversation import (
    ConversationModel,
    MessageModel,
)
from app.modules.agent.infrastructure.db.conversations import Conversations
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.channel_answer_lines import (
    COULD_NOT_ANSWER,
    STOPPED_BEFORE_ANSWERING,
    post_answer,
    post_why_unanswered,
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
)
from app.modules.channels.contracts.answers import (
    line_answered_in,
    lines_of_run,
    answers_of_run,
    settle_answers,
    start_answering,
)
from app.modules.channels.contracts.backgrounds import (
    BACKGROUND_KEY,
    BackgroundRefs,
    BackgroundView,
    refs_of,
)
from app.modules.channels.contracts.live import announce_answering
from app.modules.channels.contracts.lines import (
    last_heard_line,
    line_by_id,
    member_background,
)

logger = get_logger(__name__)

#: The most lines of the channel a run answering a line is shown.
IN_APP_CONTEXT_LINES = 30
#: The most messages one run is taken to have read: a burst steered into it.
MAX_LINES_A_RUN_READS = 50


class LineWaits(Exception):
    """The line is in another thread of a group than the run its agent is
    working on there: it waits for that run to end, and starts its own."""


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
    """A member's own line written in Lemma -- wherever it was carried -- still
    there. One heard on a platform is answered there. A members' note is
    answered too, by the agents it names, as a note (``channel_answer_lines``)."""
    return (
        line.sender.kind is SenderKind.MEMBER
        and line.sender.user_id is not None
        and not line.heard_outside
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
            user_id=user_id,
            agent_id=agent_id,
            agent_name=agent_name,
        )
        if await _answering_another_thread(uow, channel, line, conversation_id):
            raise LineWaits(line.id)
        await start_answering(
            uow,
            message_id=line.id,
            channel_id=line.channel_id,
            agent_id=agent_id,
            conversation_id=conversation_id,
        )
        background = await member_background(uow, line=line, limit=IN_APP_CONTEXT_LINES)
        started = await SendMessage.build(uow).send(
            conversation_id=conversation_id,
            user_id=user_id,
            content=line.text,
            pod_id=pod_id,
            agent_name=agent_name,
            message_metadata=turn_metadata(channel, line, background),
            require_execute_grant=False,
            client_message_id=turn_client_id(line.id),
            kind=RunKind.CHANNEL,
        )
        # The run that answers it: the one this turn started, or the one
        # already going that it joined.
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
            agent_run_id=started.agent_run_id,
            stream=(CONVERSATION_STREAM, str(conversation_id)),
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


async def _answering_another_thread(
    uow: SqlAlchemyUnitOfWork,
    channel: Channel,
    line: ChannelMessage,
    conversation_id: UUID,
) -> bool:
    """Whether the member's conversation in a group is busy answering a line
    in another thread. Joining that run would answer this line under that
    one; it waits instead. In a member's own channel, which reads as a chat,
    a line said meanwhile joins the run, as a person adds to what they asked.

    Under the conversation's lock, taken as a turn takes it: the run ending
    meanwhile either ended before this read or waits for this transaction,
    so a line found waiting is never one its run's end has already passed by.
    """
    if channel.kind is not ChannelKind.GROUP:
        return False
    conversations = Conversations(uow)
    runs = Runs(uow)
    await conversations.lock_conversation(conversation_id)
    running = await runs.get_active_agent_run_for_update(conversation_id)
    if running is None:
        return False
    answering = [
        found
        for line_id in await lines_of_run(uow, running.id)
        if line_id != line.id and (found := await line_by_id(uow, line_id))
    ]
    if not answering:
        return False
    return all(_thread_of(other) != _thread_of(line) for other in answering)


def _thread_of(line: ChannelMessage) -> UUID:
    return line.thread_root_id or line.id


async def _members_conversation(
    uow: SqlAlchemyUnitOfWork,
    *,
    channel: Channel,
    user_id: UUID,
    agent_id: UUID,
    agent_name: str,
) -> UUID:
    seats = ChannelConversations(uow)
    await seats.lock_person(
        channel_id=channel.id,
        pod_id=channel.pod_id,
        agent_id=agent_id,
        person_id=user_id,
    )
    found = await seats.in_channel(
        channel_id=channel.id,
        pod_id=channel.pod_id,
        agent_id=agent_id,
        person_id=user_id,
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
    )
    return opened.id


def turn_metadata(
    channel: Channel, line: ChannelMessage, background: Sequence[ChannelMessage]
) -> dict[str, object]:
    metadata: dict[str, object] = {
        "source": LEMMA_CHANNEL,
        "channel_id": str(channel.id),
        "channel_message_id": str(line.id),
    }
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
            view=BackgroundView.MEMBER_IN_LEMMA,
            user_id=line.sender.user_id,
            items=refs_of(background),
        ).model_dump(mode="json")
    return metadata


# -- the answer ------------------------------------------------------------------


async def post_run_answer(
    uow: SqlAlchemyUnitOfWork,
    *,
    run_id: UUID,
    conversation_id: UUID,
    answered: bool = True,
    paused: bool = False,
) -> ChannelMessage | None:
    """A finished run's answer, as the agent's line in the thread of the
    oldest line in Lemma it answers. Nothing for a run answering none -- but
    a run woken by the answers of people the agent asked for the member
    answers that member, after the line that set it asking
    (``_post_handed_back``).

    ``answered``: the run ended with an answer, rather than failing, stopping
    or pausing; either way the lines' readers hear they are no longer being
    answered, once this commits, and a run that failed or was stopped says
    so in the answer's place. ``paused``: it is waiting on somebody, and the
    run that carries it on answers the same lines.
    """
    read, carries_on, handed_back = await _lines_read_by(uow, run_id, conversation_id)
    answers = await answers_of_run(
        uow, conversation_id=conversation_id, line_ids=read, carries_on=carries_on
    )
    conversation = await uow.session.get(ConversationModel, conversation_id)
    if conversation is None:
        return None
    if not answers:
        if handed_back and answered and not paused:
            return await _post_handed_back(
                uow, run_id=run_id, conversation=conversation
            )
        return None
    lines = [await line_by_id(uow, answer.message_id) for answer in answers]
    said = [line for line in lines if line is not None]
    if conversation.user_id is not None:
        for line in said:
            announce_answering(
                pod_id=conversation.pod_id,
                channel_id=line.channel_id,
                message_id=line.id,
                agent_id=answers[0].agent_id,
                for_user_id=conversation.user_id,
                state="finished",
                after_commit_of=uow.session,
            )
    if paused:
        return None
    posted: ChannelMessage | None = None
    if said and not said[0].is_deleted:
        material = await run_reply_material(uow, run_id)
        if answered:
            posted = await post_answer(
                uow,
                run_id=run_id,
                conversation=conversation,
                line=said[0],
                material=material,
            )
        else:
            await post_why_unanswered(
                uow,
                run_id=run_id,
                conversation=conversation,
                line=said[0],
                material=material,
            )
    await settle_answers(
        uow, answers, run_id=run_id, answered_with=posted.id if posted else None
    )
    return posted


async def _lines_read_by(
    uow: SqlAlchemyUnitOfWork, run_id: UUID, conversation_id: UUID
) -> tuple[list[UUID], bool, bool]:
    """The lines in Lemma whose words this run read -- the one that started
    it and any steered into it, oldest first -- whether it carries on a
    paused run, which no message of its own started, and whether it read the
    answers to what its conversation asked of somebody else.

    Not a message that joined it and was never read: the run that delivers it
    answers it (``StartQueuedFollowUp``).
    """
    metadata = MessageModel.message_metadata
    started_it = (MessageModel.agent_run_id == run_id) & (
        MessageModel.queue_state == QueueState.NONE
    )
    rows = (
        await uow.session.execute(
            select(
                metadata["source"].astext,
                metadata["channel_message_id"].astext,
                started_it,
            )
            .where(
                MessageModel.conversation_id == conversation_id,
                MessageModel.role == "user",
                started_it | (MessageModel.steered_into_run_id == run_id),
            )
            .order_by(MessageModel.sequence)
            .limit(MAX_LINES_A_RUN_READS)
        )
    ).all()
    lines: list[UUID] = []
    for source, line_id, _ in rows:
        if source == LEMMA_CHANNEL and line_id:
            try:
                lines.append(UUID(line_id))
            except ValueError:
                continue
    handed_back = any(row[0] in HANDED_BACK_SOURCES for row in rows)
    return lines, not any(row[2] for row in rows), handed_back


async def _post_handed_back(
    uow: SqlAlchemyUnitOfWork, *, run_id: UUID, conversation: ConversationModel
) -> ChannelMessage | None:
    """The answer of a run woken by the people the agent asked, in a member's
    conversation in a channel of Lemma: after the line the conversation took
    up last, in its thread -- where the member asked it to ask them.

    Only where the member last spoke to it in Lemma: one who last wrote on a
    platform is answered there by ``agent_surfaces``, by the same rule
    (``last_heard_line``) -- their conversation is the same one either way.
    """
    if conversation.user_id is None or conversation.contact_id is not None:
        return None
    heard = await last_heard_line(uow, conversation.id)
    if heard is None or heard.source_id is not None:
        return None
    line_id = await line_answered_in(uow, conversation.id)
    line = await line_by_id(uow, line_id) if line_id else None
    if line is None or line.is_deleted:
        return None
    return await post_answer(
        uow,
        run_id=run_id,
        conversation=conversation,
        line=line,
        material=await run_reply_material(uow, run_id),
    )


__all__ = [
    "COULD_NOT_ANSWER",
    "IN_APP_CONTEXT_LINES",
    "LineWaits",
    "STOPPED_BEFORE_ANSWERING",
    "answer_client_id",
    "answer_line",
    "turn_client_id",
    "post_run_answer",
    "turn_metadata",
]
