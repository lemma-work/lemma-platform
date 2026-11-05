"""A finished run's answer, at the place of what it answered.

One rule for every run of a person's conversation, wherever they wrote: the
lines a run answers are the ones whose words it read -- the message that
started it and any steered into it -- each recorded, as a turn took it up, as
owed an answer by the conversation's agent (``channels.contracts.answers``).
The run answers at their place: in Lemma, under the oldest of them, where the
member asked; on a platform, with a delivery there, in answer to the latest
of them. A member who wrote L1 and then L2 while the agent worked has one
answer, where they asked; never wherever the conversation happened to hear
last.

A run that read no line answers where its conversation last took one up --
one woken by the answers of people the agent asked for the member, or one
asked in the conversation itself -- and before any line was taken up, where
the person last wrote on a platform. A run asked at a platform place whose
lines were taken up before that was recorded answers at that place. Lemma
hears a run's end either way: its lines' readers are told they are no longer
being answered, and a run that failed or was stopped says so in Lemma, in the
answer's place. A platform hears only an answer, or a failure's notice.

Safe to run twice: the answer is posted with the run as its client id, so a
retried post is the same line, and its delivery is unique per place.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts.channel_replies import (
    RunReplyMaterial,
    run_reply_material,
)
from app.modules.agent.domain.pod_asks import HANDED_BACK_SOURCES
from app.modules.agent.domain.private_notes import HEARD_IN_CHANNEL, LEMMA_CHANNEL
from app.modules.agent.domain.run_places import ASKED_IN_PLACE_KEY, place_source
from app.modules.agent.domain.value_objects import QueueState
from app.modules.agent.infrastructure.models.conversation import (
    ConversationModel,
    MessageModel,
)
from app.modules.agent.services.channel_answer_lines import (
    post_answer,
    post_why_unanswered,
)
from app.modules.channels.contracts import ChannelMessage, source_by_id
from app.modules.channels.contracts.answers import (
    answers_of_run,
    line_answered_in,
    settle_answers,
)
from app.modules.channels.contracts.live import announce_answering
from app.modules.channels.contracts.lines import line_by_id, line_heard_at
from app.modules.channels.contracts.places import ReplyAddress, reply_address

#: The most messages one run is taken to have read: a burst steered into it.
MAX_LINES_A_RUN_READS = 50

#: How a run ended that a platform is told of: its answer, or its failure.
_SAID_ON_A_PLATFORM = frozenset({"COMPLETED", "FAILED"})


@dataclass(frozen=True, slots=True)
class _Read:
    """What a run read: the lines, oldest first; whether it carries on a
    paused run, which no message of its own started; and whether it read the
    answers to what its conversation asked of somebody else."""

    lines: list[UUID]
    carries_on: bool
    handed_back: bool
    #: The heard lines, as found by where they were heard: not read again.
    heard: dict[UUID, ChannelMessage]


async def post_run_answer(
    uow: SqlAlchemyUnitOfWork,
    *,
    run_id: UUID,
    conversation_id: UUID,
    answered: bool = True,
    paused: bool = False,
) -> ChannelMessage | None:
    """A finished run's answer, at the place of the lines it answers.

    ``answered``: the run ended with an answer, rather than failing, stopping
    or pausing; either way the lines' readers in Lemma hear they are no
    longer being answered, once this commits, and a run that failed or was
    stopped says so in the answer's place there. ``paused``: it is waiting
    on somebody, and the run that carries it on answers the same lines.
    """
    conversation = await uow.session.get(ConversationModel, conversation_id)
    if conversation is None:
        return None
    read = await _lines_read_by(uow, run_id, conversation_id)
    answers = await answers_of_run(
        uow,
        conversation_id=conversation_id,
        line_ids=read.lines,
        carries_on=read.carries_on,
    )
    said = [
        line
        for answer in answers
        if (
            line := read.heard.get(answer.message_id)
            or await line_by_id(uow, answer.message_id)
        )
        is not None
    ]
    in_lemma = [line for line in said if line.source_id is None]
    if conversation.user_id is not None:
        for line in in_lemma:
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
    material = await run_reply_material(uow, run_id)
    if material is None:
        return None
    if in_lemma:
        posted = await _answer_in_lemma(
            uow,
            run_id=run_id,
            conversation=conversation,
            line=in_lemma[0],
            material=material,
            answered=answered,
        )
    elif said:
        posted = await _answer_at_place(
            uow,
            run_id=run_id,
            conversation=conversation,
            material=material,
            address=await _address_of(uow, said[-1]),
            expected_at=said[-1].source_id,
        )
    elif answers:
        # What it answered is gone: there is nowhere to say it.
        posted = None
    else:
        posted = await _answer_unasked(
            uow,
            run_id=run_id,
            conversation=conversation,
            material=material,
            handed_back=read.handed_back and answered,
        )
    # Answered only by an answer: a line saying why there is none -- or a
    # platform's failure notice -- ends the lines unanswered.
    await settle_answers(
        uow,
        answers,
        run_id=run_id,
        answered_with=posted.id if posted is not None and answered else None,
    )
    return posted


async def _answer_in_lemma(
    uow: SqlAlchemyUnitOfWork,
    *,
    run_id: UUID,
    conversation: ConversationModel,
    line: ChannelMessage,
    material: RunReplyMaterial,
    answered: bool,
) -> ChannelMessage | None:
    """The answer under the line in Lemma, or why there is none."""
    if line.is_deleted:
        return None
    if answered:
        return await post_answer(
            uow, run_id=run_id, conversation=conversation, line=line, material=material
        )
    return await post_why_unanswered(
        uow, run_id=run_id, conversation=conversation, line=line, material=material
    )


async def _answer_unasked(
    uow: SqlAlchemyUnitOfWork,
    *,
    run_id: UUID,
    conversation: ConversationModel,
    material: RunReplyMaterial,
    handed_back: bool,
) -> ChannelMessage | None:
    """The answer of a run that read no line: at the place it was asked, else
    where its conversation last took a line up -- in Lemma only for a run
    woken by the answers of the people the agent asked, after the line that
    set it asking -- else where the person last wrote on a platform."""
    if material.asked_at is not None:
        return await _answer_at_place(
            uow,
            run_id=run_id,
            conversation=conversation,
            material=material,
            address=await reply_address(
                uow, conversation.id, at_source_id=material.asked_at
            ),
            expected_at=material.asked_at,
        )
    taken_up = await line_answered_in(uow, conversation.id)
    line = await line_by_id(uow, taken_up) if taken_up else None
    if line is None:
        return await _answer_at_place(
            uow,
            run_id=run_id,
            conversation=conversation,
            material=material,
            address=await reply_address(uow, conversation.id),
        )
    if line.source_id is not None:
        return await _answer_at_place(
            uow,
            run_id=run_id,
            conversation=conversation,
            material=material,
            address=await _address_of(uow, line),
            expected_at=line.source_id,
        )
    if not handed_back or conversation.contact_id is not None or line.is_deleted:
        return None
    return await post_answer(
        uow, run_id=run_id, conversation=conversation, line=line, material=material
    )


async def _answer_at_place(
    uow: SqlAlchemyUnitOfWork,
    *,
    run_id: UUID,
    conversation: ConversationModel,
    material: RunReplyMaterial,
    address: ReplyAddress | None,
    expected_at: UUID | None = None,
) -> ChannelMessage | None:
    """The answer at a platform place, with its delivery there. Nothing of a
    private note's run, or of one asked in Lemma, goes to a platform; and a
    run that stopped is not told of there, where nobody saw it start.

    ``expected_at``: the place it must be said at -- the one it was asked
    at, or the one its line was heard at. Gone while the run worked, the
    answer is never said at another place the person wrote from, and never
    dropped: it stays on the conversation's channel, sent nowhere."""
    if (
        material.private
        or material.asked_in_lemma
        or material.status not in _SAID_ON_A_PLATFORM
    ):
        return None
    from app.modules.agent_surfaces.contracts.egress import (
        post_answer_at_place,
        post_unplaced_answer,
    )

    if address is None:
        if expected_at is None:
            # Said nowhere outside Lemma: the conversation shows its answer.
            return None
        return await post_unplaced_answer(
            uow,
            run_id=run_id,
            conversation_id=conversation.id,
            material=material,
            gone_source_id=expected_at,
        )
    return await post_answer_at_place(
        uow,
        run_id=run_id,
        conversation_id=conversation.id,
        address=address,
        material=material,
    )


async def _address_of(
    uow: SqlAlchemyUnitOfWork, line: ChannelMessage
) -> ReplyAddress | None:
    """What answers a heard line in place: its place, and its reply target --
    else the place's own, for a line that kept none."""
    if line.source_id is None:
        return None
    source = await source_by_id(uow, line.source_id)
    if source is None:
        return None
    return ReplyAddress(source=source, ref=line.reply_ref or source.delivery_ref)


async def _lines_read_by(
    uow: SqlAlchemyUnitOfWork, run_id: UUID, conversation_id: UUID
) -> _Read:
    """The lines whose words this run read -- the one that started it and
    any steered into it, oldest first: a line a turn started from the
    channel by its id (written in Lemma, or heard and answered by an agent
    other than its door's), one a door took up by where it was heard and its
    id there.

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
                metadata[ASKED_IN_PLACE_KEY].astext,
                metadata["external_message_id"].astext,
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
    heard_lines: dict[UUID, ChannelMessage] = {}
    for source, line_id, place, external_message_id, _ in rows:
        heard_at = place_source(place)
        if source in (LEMMA_CHANNEL, HEARD_IN_CHANNEL) and line_id:
            try:
                lines.append(UUID(line_id))
            except ValueError:
                continue
        elif heard_at is not None and external_message_id:
            heard = await line_heard_at(
                uow, source_id=heard_at, external_message_id=external_message_id
            )
            if heard is not None:
                lines.append(heard.id)
                heard_lines[heard.id] = heard
    return _Read(
        lines=lines,
        carries_on=not any(row[4] for row in rows),
        handed_back=any(row[0] in HANDED_BACK_SOURCES for row in rows),
        heard=heard_lines,
    )


__all__ = ["MAX_LINES_A_RUN_READS", "post_run_answer"]
