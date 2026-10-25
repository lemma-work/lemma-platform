"""How an agent's answer in Lemma ends, whichever way it ends.

Against Postgres and the work runtime, with the model's part played by hand,
as in ``test_channel_answers_e2e``: a run is started by a member's line,
finished by hand, and everything around it -- the answer, the word that there
is none, the turn put off, the asked person's answer coming back -- runs for
real.

* An answer that comes back from somebody the agent asked reaches the member
  who set it asking, as a line after their own.
* In a group, a second line is its own thread: it waits for the run on the
  first, and is answered by its own run, in its own thread.
* A run that fails, is stopped, or is refused before it starts says so where
  it was asked; a contact whose run failed is called a person for, and is
  not counted as answered.
* What the model reads of a member's line is what it says now.
* A note naming an agent is answered as a note.
* A line after a stopped request is answered for itself: the model reads the
  stopped request as stopped, not as work to finish.
"""

from __future__ import annotations

from functools import partial
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.core.infrastructure.work import WorkClass, WorkRegistry, facts
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.domain.asked_questions import batch_of, message_ref
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.events.channel_answer_work import register_channel_answer_work
from app.modules.agent.events.channel_question_work import (
    register_channel_question_work,
)
from app.modules.agent.events.run_followup_work import register_run_followup_work
from app.modules.agent.infrastructure.models.conversation import (
    AgentRunModel,
    ConversationModel,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    history_and_prompt,
)
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.db.history_reads import HistoryReads
from app.modules.agent.services.runtime_history import MAX_HISTORY_AGENT_RUNS
from app.modules.agent.services.channel_answers import (
    COULD_NOT_ANSWER,
    STOPPED_BEFORE_ANSWERING,
)
from app.modules.agent.services.channel_background import (
    DELETED_LINE,
    with_channel_background,
)
from app.modules.agent.services.contact_run_failures import RUN_FAILED_REASON
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.services.message_reply_service import REPLY_SOURCE
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.services.runtime_history import (
    STOPPED_BY_MEMBER,
    assemble_runtime_history,
)
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    conversation_service,
    messages,
    owner_uow,
    running_run,
    runs,
    seeded_conversation,
)
from app.modules.channels.contracts import (
    AgentAnswers,
    Asker,
    ChannelMessage,
    NewLine,
    NewQuestion,
    Principal,
    QuestionKind,
    Sender,
    channel_by_id,
    contact_channel,
    direct_channel,
    erase_line,
    post,
    revise_line,
)
from app.modules.channels.contracts import questions as channel_questions
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.models import (
    ChannelLineAnswerModel,
    ChannelMessageModel,
    ChannelModel,
)
from app.modules.channels.infrastructure.case_models import ChannelCaseModel
from app.modules.channels.tests.e2e import channel_pod
from app.modules.channels.tests.e2e.channel_pod import Pod, group
from app.modules.contacts.contracts import (
    IdentityKind,
    IdentityStrength,
    contact_by_id,
    note_answered,
    open_contact,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

#: The channels tests' pod: an owner, an editor, a user and a viewer.
pod = channel_pod.pod


# ------------------------------------------------------------------- harness


def _uows() -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


async def _says(
    channel_id: UUID, user_id: UUID, text: str, *, members_only: bool = False
) -> ChannelMessage:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        channel = await channel_by_id(uow, channel_id)
        assert channel is not None
        line = await post(
            uow,
            channel_id=channel_id,
            line=NewLine(
                sender=Sender.member(user_id), text=text, members_only=members_only
            ),
            mentioned_agents=[channel.pod_id] if members_only else (),
        )
        await uow.commit()
    assert line is not None
    return line


async def _direct(pod_id: UUID, user_id: UUID) -> UUID:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        direct = await direct_channel(uow, pod_id=pod_id, user_id=user_id)
        await uow.commit()
    return direct.id


async def _drain(*classes: WorkClass) -> None:
    registry = WorkRegistry()
    register_channel_answer_work(registry)
    register_channel_question_work(registry)
    register_run_followup_work(registry)
    await make_due(async_session_maker)
    with suppress_agent_run_enqueue():
        await run_until_idle(
            async_session_maker, registry=registry, classes=list(classes)
        )


async def _answering(line_id: UUID) -> ChannelLineAnswerModel | None:
    async with async_session_maker() as session:
        return await session.scalar(
            select(ChannelLineAnswerModel).where(
                ChannelLineAnswerModel.message_id == line_id
            )
        )


async def _run_answering(line_id: UUID) -> tuple[UUID, UUID]:
    answer = await _answering(line_id)
    assert answer is not None and answer.agent_run_id is not None, "not answered"
    return answer.conversation_id, answer.agent_run_id


async def _end(
    run_id: UUID,
    *,
    pod_id: UUID,
    user_id: UUID,
    status: AgentRunStatus = AgentRunStatus.COMPLETED,
    answer: str | None = None,
    error: str | None = None,
) -> None:
    """The run ends as the runner leaves it, and says so."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversation_id = await uow.session.scalar(
            select(AgentRunModel.conversation_id).where(AgentRunModel.id == run_id)
        )
        assert conversation_id is not None
        await Runs(uow).finish_agent_run(
            agent_run_id=run_id,
            status=status,
            conversation_status=ConversationStatus(status.value),
            output_data=answer,
            error=error,
        )
        await facts.emit(
            uow,
            AgentRunFinished(
                run_id=run_id,
                conversation_id=conversation_id,
                pod_id=pod_id,
                user_id=user_id,
                status=status.value,
                conversation_status=status.value,
                origin="chat",
            ),
        )
        await uow.commit()
    await _drain(WorkClass.EVENTS)


async def _said_by_run(run_id: UUID) -> list[ChannelMessageModel]:
    async with async_session_maker() as session:
        return list(
            await session.scalars(
                select(ChannelMessageModel).where(
                    ChannelMessageModel.agent_run_id == run_id
                )
            )
        )


async def _replies_to(line_id: UUID) -> list[ChannelMessageModel]:
    async with async_session_maker() as session:
        return list(
            await session.scalars(
                select(ChannelMessageModel)
                .where(ChannelMessageModel.reply_to_id == line_id)
                .order_by(ChannelMessageModel.seq)
            )
        )


# --------------------------------------------- an answer the agent asked for


async def test_an_answer_the_agent_asked_for_comes_back_to_the_member(pod: Pod):
    """The member asks the agent to ask the editor; the editor answers; the
    run that wakes with the news answers the member, after their line."""
    pod_id, owner = UUID(pod.id), UUID(pod.owner["id"])
    editor = UUID(pod.editor["id"])
    direct = await _direct(pod_id, owner)
    asked = await _says(direct, owner, "Ask the editor when we ship.")
    await _drain(WorkClass.INTERACTIVE)
    conversation_id, first = await _run_answering(asked.id)

    # The run asks the editor, as ``channel_post(wants_answer=True)`` does.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        editors = await direct_channel(uow, pod_id=pod_id, user_id=editor)
        question = await post(
            uow,
            channel_id=editors.id,
            line=NewLine(
                sender=Sender.agent(pod_id, on_behalf_of=Principal.member(owner)),
                text="When do we ship?",
                conversation_id=conversation_id,
                agent_run_id=first,
            ),
            question=NewQuestion(
                kind=QuestionKind.ASK,
                ref=message_ref(conversation_id, "call-1"),
                asker=Asker.message(conversation_id, "call-1"),
                question={"tool": "message", "prompt": "When do we ship?"},
                awaits=Principal.member(editor),
                batch_id=batch_of(first),
            ),
        )
        await uow.commit()
    assert question is not None
    await _end(first, pod_id=pod_id, user_id=owner, answer="I asked the editor.")

    await channel_questions.answer(
        _uows(),
        message_id=question.id,
        by=Principal.member(editor),
        given={"text": "Tuesday"},
    )
    await _drain(WorkClass.EVENTS)
    woken = [run.id for run in await runs(conversation_id) if run.id != first]
    assert len(woken) == 1, "the editor's answer did not wake the conversation"
    news = [
        message
        for message in await messages(conversation_id)
        if (message.metadata or {}).get("source") == REPLY_SOURCE
    ]
    assert news and news[0].agent_run_id == woken[0]

    await _end(
        woken[0], pod_id=pod_id, user_id=owner, answer="The editor said: Tuesday."
    )

    [reply] = await _said_by_run(woken[0])
    assert reply.body == "The editor said: Tuesday."
    assert reply.channel_id == direct
    assert reply.reply_to_id == asked.id
    assert reply.on_behalf_user_id == owner


# --------------------------------------------------- two lines in a group


async def test_two_lines_in_a_group_are_answered_each_by_its_own_answer(pod: Pod):
    """With the agent answering every line, "say OK1" and then "say OK2"
    while it works on the first: the second is not steered into the first's
    run -- whose one answer would answer OK1 alone -- but waits, and its own
    run answers it. A group made in Lemma reads as a chat: each answer is in
    the main timeline, pointing at the line it answers."""
    pod_id, owner = UUID(pod.id), UUID(pod.owner["id"])
    made = await group(pod, title="launch")
    channel_id = UUID(made["id"])
    async with create_uow_from_session_maker(async_session_maker) as uow:
        channel = await channel_by_id(uow, channel_id)
        assert channel is not None
        await MemberRepository(uow.session).add_agent(
            channel, pod_id, answers=AgentAnswers.EVERY_LINE
        )
        await uow.commit()

    one = await _says(channel_id, owner, "say OK1")
    await _drain(WorkClass.INTERACTIVE)
    _, first = await _run_answering(one.id)
    two = await _says(channel_id, owner, "say OK2")
    await _drain(WorkClass.INTERACTIVE)
    assert await _answering(two.id) is None, "OK2 joined the run answering OK1"

    await _end(first, pod_id=pod_id, user_id=owner, answer="OK1")
    [answer_one] = await _said_by_run(first)
    assert (answer_one.body, answer_one.thread_root_id, answer_one.reply_to_id) == (
        "OK1",
        None,
        one.id,
    )

    await _drain(WorkClass.INTERACTIVE)
    _, second = await _run_answering(two.id)
    assert second != first
    await _end(second, pod_id=pod_id, user_id=owner, answer="OK2")
    [answer_two] = await _said_by_run(second)
    assert (answer_two.body, answer_two.thread_root_id, answer_two.reply_to_id) == (
        "OK2",
        None,
        two.id,
    )


async def test_a_reply_in_the_same_thread_still_joins_the_run(pod: Pod):
    pod_id, owner = UUID(pod.id), UUID(pod.owner["id"])
    channel_id = UUID((await group(pod, title="ops"))["id"])
    async with create_uow_from_session_maker(async_session_maker) as uow:
        channel = await channel_by_id(uow, channel_id)
        assert channel is not None
        await MemberRepository(uow.session).add_agent(
            channel, pod_id, answers=AgentAnswers.EVERY_LINE
        )
        await uow.commit()
    one = await _says(channel_id, owner, "What broke?")
    await _drain(WorkClass.INTERACTIVE)
    _, first = await _run_answering(one.id)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        more = await post(
            uow,
            channel_id=channel_id,
            line=NewLine(
                sender=Sender.member(owner), text="And when?", thread_root_id=one.id
            ),
        )
        await uow.commit()
    assert more is not None
    await _drain(WorkClass.INTERACTIVE)

    assert (await _run_answering(more.id))[1] == first


# ------------------------------------------------- failed, stopped, refused


@pytest.mark.parametrize(
    ("status", "error", "said"),
    [
        (
            AgentRunStatus.FAILED,
            "The model provider is unavailable right now.",
            "I couldn't answer this: The model provider is unavailable right now.",
        ),
        (AgentRunStatus.FAILED, None, COULD_NOT_ANSWER),
        (AgentRunStatus.STOPPED, None, STOPPED_BEFORE_ANSWERING),
    ],
)
async def test_a_run_that_did_not_answer_says_so_where_it_was_asked(
    pod: Pod, status: AgentRunStatus, error: str | None, said: str
):
    pod_id, owner = UUID(pod.id), UUID(pod.owner["id"])
    direct = await _direct(pod_id, owner)
    asked = await _says(direct, owner, "What is on today?")
    await _drain(WorkClass.INTERACTIVE)
    _, run_id = await _run_answering(asked.id)

    await _end(run_id, pod_id=pod_id, user_id=owner, status=status, error=error)

    [notice] = await _replies_to(asked.id)
    assert notice.body == said
    assert notice.sender_kind == "AGENT"
    assert (await _answering(asked.id)).state == "ENDED"  # type: ignore[union-attr]


async def test_a_line_refused_before_its_run_starts_says_why(
    pod: Pod, monkeypatch: pytest.MonkeyPatch
):
    async def refuse(self, **_):
        raise DomainError(
            "You don't have access to this agent.", code="FORBIDDEN", status_code=403
        )

    monkeypatch.setattr(ConversationService, "authorize_opening", refuse)
    pod_id, owner = UUID(pod.id), UUID(pod.owner["id"])
    direct = await _direct(pod_id, owner)
    asked = await _says(direct, owner, "What is on today?")
    await _drain(WorkClass.INTERACTIVE)
    await _drain(WorkClass.INTERACTIVE)  # retried: still one line

    [notice] = await _replies_to(asked.id)
    assert notice.body == "I can't answer this: You don't have access to this agent."
    assert await _answering(asked.id) is None


# -------------------------------------------------- a contact's failed run


async def test_a_contacts_failed_run_calls_for_a_person_and_does_not_count(
    pod: Pod,
):
    pod_id, keeper = UUID(pod.id), UUID(pod.owner["id"])
    async with create_uow_from_session_maker(async_session_maker) as uow:
        contact = await open_contact(
            uow,
            pod_id=pod_id,
            kind=IdentityKind.EMAIL,
            value=f"dana-{uuid4().hex[:8]}@example.com",
            strength=IdentityStrength.CHANNEL,
            display_name="Dana",
        )
        channel = await contact_channel(uow, pod_id=pod_id, contact_id=contact.id)
        # Let in, as ``outside_cap`` lets a contact's turn in: marked answered.
        await note_answered(uow, pod_id=pod_id, contact_id=contact.id)
        await uow.commit()
    # The contact's conversation, as a surface opens one for them.
    seeded = await seeded_conversation(pod.scenario)
    async with async_session_maker() as session:
        await session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == seeded.conversation_id)
            .values(contact_id=contact.id, channel_id=channel.id)
        )
        await session.commit()
    run_id = await running_run(seeded)

    await _end(
        run_id,
        pod_id=pod_id,
        user_id=keeper,
        status=AgentRunStatus.FAILED,
        error="Usage reporting is unavailable.",
    )

    async with create_uow_from_session_maker(async_session_maker) as uow:
        reason = await uow.session.scalar(
            select(ChannelCaseModel.attention_reason)
            .join(ChannelModel, ChannelModel.current_case_id == ChannelCaseModel.id)
            .where(ChannelModel.id == channel.id)
        )
        found = await contact_by_id(uow, pod_id=pod_id, contact_id=contact.id)
    assert reason == RUN_FAILED_REASON
    assert found is not None and not found.answered


# ----------------------------------------------------------- edited, deleted


async def test_the_model_reads_a_members_line_as_it_says_now(pod: Pod):
    """Edited, the model reads the new words; deleted, that it was deleted --
    never what either said before."""
    pod_id, owner = UUID(pod.id), UUID(pod.owner["id"])
    direct = await _direct(pod_id, owner)
    code = await _says(direct, owner, "My locker code is 4417")
    await _drain(WorkClass.INTERACTIVE)
    conversation_id, first = await _run_answering(code.id)
    await _end(first, pod_id=pod_id, user_id=owner, answer="Noted.")
    word = await _says(direct, owner, "The secret word is PINEAPPLE")
    await _drain(WorkClass.INTERACTIVE)
    _, second = await _run_answering(word.id)
    await _end(second, pod_id=pod_id, user_id=owner, answer="Noted.")

    async with create_uow_from_session_maker(async_session_maker) as uow:
        await revise_line(
            uow, message_id=code.id, text="My locker code is 9900", parts=()
        )
        await erase_line(uow, message_id=word.id)
        await uow.commit()
    ask = await _says(direct, owner, "What are my code and word?")
    await _drain(WorkClass.INTERACTIVE)
    _, third = await _run_answering(ask.id)

    # As the runner builds the run's history (``AgentRunnerService``).
    async with create_uow_from_session_maker(async_session_maker) as uow:
        history_reads = HistoryReads(uow)
        read = await assemble_runtime_history(
            history_reads,
            await history_reads.load_runtime_history_digests_by_run_id(
                third, limit=MAX_HISTORY_AGENT_RUNS
            ),
            conversation_id=conversation_id,
            run_id=third,
            read_channel=partial(with_channel_background, uow),
        )
    said = [m.text or "" for m in read if m.role is MessageRole.USER]
    model_reads = "\n".join(said)
    assert "4417" not in model_reads and "PINEAPPLE" not in model_reads
    assert "My locker code is 9900" in said
    assert DELETED_LINE in said
    assert said[-1] == "What are my code and word?"


# ----------------------------------------------------------- a members' note


async def test_a_note_naming_the_agent_is_answered_as_a_note(pod: Pod):
    pod_id, owner = UUID(pod.id), UUID(pod.owner["id"])
    channel_id = UUID((await group(pod, title="notes"))["id"])
    note = await _says(
        channel_id, owner, "@agent what's our refund rule?", members_only=True
    )
    await _drain(WorkClass.INTERACTIVE)
    _, run_id = await _run_answering(note.id)
    await _end(run_id, pod_id=pod_id, user_id=owner, answer="Within 30 days.")

    [answer] = await _said_by_run(run_id)
    assert answer.body == "Within 30 days."
    assert answer.members_only is True
    assert (answer.thread_root_id, answer.reply_to_id) == (None, note.id)


# ------------------------------------------------- a line after a stopped one


async def _model_reads(conversation_id: UUID, run_id: UUID):
    """The history and prompt ``run_id``'s model is sent, built as the runner
    builds them."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        history_reads = HistoryReads(uow)
        read = await assemble_runtime_history(
            history_reads,
            await history_reads.load_runtime_history_digests_by_run_id(
                run_id, limit=MAX_HISTORY_AGENT_RUNS
            ),
            conversation_id=conversation_id,
            run_id=run_id,
            read_channel=partial(with_channel_background, uow),
        )
    return history_and_prompt(read, current_run_id=run_id)


async def test_a_line_after_a_stopped_request_is_answered_for_itself(pod: Pod):
    """Asked for an essay, stopped half way, then asked "what is 2+2?" in a
    note: the run answering the note reads the essay as stopped -- closed,
    after the work it got through -- and the note as what it is asked. It
    used to read an essay half written, finish it, and post "Done" under
    the note."""
    pod_id, owner = UUID(pod.id), UUID(pod.owner["id"])
    channel_id = UUID((await group(pod, title="essays"))["id"])
    async with create_uow_from_session_maker(async_session_maker) as uow:
        essay = await post(
            uow,
            channel_id=channel_id,
            line=NewLine(
                sender=Sender.member(owner),
                text="@agent write a 3000-word essay on bridges to a file",
            ),
            mentioned_agents=[pod_id],
        )
        await uow.commit()
    assert essay is not None
    await _drain(WorkClass.INTERACTIVE)
    conversation_id, essay_run = await _run_answering(essay.id)

    # Half way: the file write is in flight when the member presses Stop.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await Messages(uow).append_message(
            conversation_id=conversation_id,
            agent_run_id=essay_run,
            draft=MessageDraft.of_tool_call(
                tool_name="write_file",
                tool_call_id="call-essay",
                tool_args={"path": "/essays/bridges.md", "content": "Bridges..."},
            ),
        )
        agent_id = await uow.session.scalar(
            select(ConversationModel.agent_id).where(
                ConversationModel.id == conversation_id
            )
        )
        await uow.commit()
    assert agent_id is not None
    stopper = Seeded(
        pod_id=pod_id,
        user_id=owner,
        agent_id=agent_id,
        agent_name="",
        conversation_id=conversation_id,
    )
    async with owner_uow(stopper) as uow:
        await conversation_service(uow).stop_conversation(
            conversation_id=conversation_id, user_id=owner, pod_id=pod_id
        )
    await _drain(WorkClass.EVENTS)
    [stopped] = [run for run in await runs(conversation_id) if run.id == essay_run]
    assert stopped.status == AgentRunStatus.STOPPED.value

    note = await _says(channel_id, owner, "@agent what is 2+2?", members_only=True)
    await _drain(WorkClass.INTERACTIVE)
    note_conversation, note_run = await _run_answering(note.id)
    assert (note_conversation, note_run != essay_run) == (conversation_id, True)

    history, prompt = await _model_reads(conversation_id, note_run)
    assert prompt is not None and "what is 2+2?" in prompt
    said = [
        str(getattr(part, "content", ""))
        for message in history
        for part in message.parts
        if getattr(part, "part_kind", "") == "user-prompt"
    ]
    asked_essay = next(i for i, text in enumerate(said) if "essay on bridges" in text)
    closed = next(i for i, text in enumerate(said) if STOPPED_BY_MEMBER in text)
    assert asked_essay < closed == len(said) - 1, said

    await _end(note_run, pod_id=pod_id, user_id=owner, answer="4")
    [answer] = await _said_by_run(note_run)
    assert (answer.body, answer.thread_root_id, answer.reply_to_id) == (
        "4",
        None,
        note.id,
    )
