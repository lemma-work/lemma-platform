"""A line in Lemma is answered in Lemma, whatever platform the person was last on.

A member has one conversation with the pod's agent in a channel, across every
place of it: what they write on WhatsApp and what they write in Lemma continue
the same one. The WhatsApp surface writes the platform it heard them on onto
that conversation, and nothing in Lemma takes it off -- so a member asking in
Lemma was answered by a run told it was on WhatsApp ("WhatsApp only shows
text"), whose widget went to their WhatsApp chat and whose typing showed there.

Against Postgres and the work runtime, with the model's part played by hand,
as in ``test_channel_answer_outcomes_e2e``:

* The run a line in Lemma starts is marked as asked there, and reads no
  platform off its conversation: no platform's guidance, tools, files or
  progress.
* An answer the agent asked somebody for still comes back to the member in
  Lemma, though the conversation was last written to from WhatsApp.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from app.modules.agent.services.surface_context import conversation_surface
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.core.infrastructure.work import WorkClass, WorkRegistry, facts
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.test_support.conversation_surfaces_double import record_place
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.domain.asked_questions import batch_of, message_ref
from app.modules.agent.domain.private_notes import run_delivers_to_surface
from app.modules.agent.domain.prompt.surface import surface_platform_guidance
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
)
from app.modules.agent.events.channel_answer_work import register_channel_answer_work
from app.modules.agent.events.channel_question_work import (
    register_channel_question_work,
)
from app.modules.agent.events.run_followup_work import register_run_followup_work
from app.modules.agent.infrastructure.models.conversation import AgentRunModel
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.services.surface_context import surface_context_for_run
from app.modules.agent.tests.e2e.lifecycle_helpers import runs
from app.modules.channels.contracts import (
    Asker,
    ChannelMessage,
    NewLine,
    NewQuestion,
    Principal,
    QuestionKind,
    Sender,
    direct_channel,
    post,
)
from app.modules.channels.contracts import questions as channel_questions
from app.modules.channels.infrastructure.models import (
    ChannelLineAnswerModel,
    ChannelMessageModel,
)
from app.modules.channels.tests.e2e import channel_pod
from app.modules.channels.tests.e2e.channel_pod import Pod

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

#: The channels tests' pod: an owner, an editor, a user and a viewer.
pod = channel_pod.pod
channel_world = channel_pod.channel_world


async def _direct(pod_id: UUID, user_id: UUID) -> UUID:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        direct = await direct_channel(uow, pod_id=pod_id, user_id=user_id)
        await uow.commit()
    return direct.id


async def _says(channel_id: UUID, user_id: UUID, text: str) -> ChannelMessage:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        line = await post(
            uow,
            channel_id=channel_id,
            line=NewLine(sender=Sender.member(user_id), text=text),
        )
        await uow.commit()
    assert line is not None
    return line


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


async def _run_answering(line_id: UUID) -> tuple[UUID, UUID]:
    async with async_session_maker() as session:
        answer = await session.scalar(
            select(ChannelLineAnswerModel).where(
                ChannelLineAnswerModel.message_id == line_id
            )
        )
    assert answer is not None and answer.agent_run_id is not None, "not answered"
    return answer.conversation_id, answer.agent_run_id


async def _end(run_id: UUID, *, pod_id: UUID, user_id: UUID, answer: str) -> None:
    """The run ends as the runner leaves it, and says so."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversation_id = await uow.session.scalar(
            select(AgentRunModel.conversation_id).where(AgentRunModel.id == run_id)
        )
        assert conversation_id is not None
        await Runs(uow).finish_agent_run(
            agent_run_id=run_id,
            status=AgentRunStatus.COMPLETED,
            conversation_status=ConversationStatus.COMPLETED,
            output_data=answer,
        )
        await facts.emit(
            uow,
            AgentRunFinished(
                run_id=run_id,
                conversation_id=conversation_id,
                pod_id=pod_id,
                user_id=user_id,
                status=AgentRunStatus.COMPLETED.value,
                conversation_status=ConversationStatus.COMPLETED.value,
                origin="chat",
            ),
        )
        await uow.commit()
    await _drain(WorkClass.EVENTS)


async def _heard_on_whatsapp(conversation_id: UUID) -> None:
    """The member writes to the pod's WhatsApp number: the surface writes what
    it heard onto their conversation, as ``conversation_binder`` does."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await record_place(
            uow,
            conversation_id,
            {
                "surface_id": str(uuid4()),
                "surface_platform": "WHATSAPP",
                "external_channel_id": "15556295168",
                "external_user_id": "919800000000",
                "conversation_kind": "DM",
            },
        )
        await uow.commit()


async def test_a_line_in_lemma_is_not_asked_on_whatsapp(pod: Pod):
    pod_id, owner = UUID(pod.id), UUID(pod.owner["id"])
    direct = await _direct(pod_id, owner)
    first = await _says(direct, owner, "Where are we at?")
    await _drain(WorkClass.INTERACTIVE)
    conversation_id, run_id = await _run_answering(first.id)
    await _end(run_id, pod_id=pod_id, user_id=owner, answer="On track.")
    await _heard_on_whatsapp(conversation_id)

    second = await _says(direct, owner, "Can you show widgets here?")
    await _drain(WorkClass.INTERACTIVE)
    same_conversation, asked_here = await _run_answering(second.id)

    assert same_conversation == conversation_id, "one conversation per person"
    [run] = [run for run in await runs(conversation_id) if run.id == asked_here]
    assert run.state().asked_in_lemma
    assert not run_delivers_to_surface(run.state())
    async with create_uow_from_session_maker(async_session_maker) as uow:
        loaded = await conversation_surface(uow, conversation_id)
    context = surface_context_for_run(loaded, run.state())
    assert context["surface_platform"] is None
    assert context["external_channel_id"] is None
    assert surface_platform_guidance(context["surface_platform"]) == ""
    # The surface's record still says WhatsApp: that is where a reply to
    # the member's next WhatsApp message goes.
    assert surface_context_for_run(loaded, None)["surface_platform"] == "WHATSAPP"


async def test_an_answer_asked_for_comes_back_in_lemma_after_whatsapp(pod: Pod):
    """The member asks in Lemma for the agent to ask the editor. Their
    conversation was last written to from WhatsApp; the editor's answer still
    comes back to them in Lemma, after the line they asked in."""
    pod_id, owner = UUID(pod.id), UUID(pod.owner["id"])
    editor = UUID(pod.editor["id"])
    direct = await _direct(pod_id, owner)
    asked = await _says(direct, owner, "Ask the editor when we ship.")
    await _drain(WorkClass.INTERACTIVE)
    conversation_id, first = await _run_answering(asked.id)
    await _heard_on_whatsapp(conversation_id)

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
        SessionUnitOfWorkFactory(async_session_maker),
        message_id=question.id,
        by=Principal.member(editor),
        given={"text": "Tuesday"},
    )
    await _drain(WorkClass.EVENTS)
    [woken] = [run.id for run in await runs(conversation_id) if run.id != first]
    await _end(woken, pod_id=pod_id, user_id=owner, answer="The editor said: Tuesday.")

    async with async_session_maker() as session:
        said = list(
            await session.scalars(
                select(ChannelMessageModel).where(
                    ChannelMessageModel.agent_run_id == woken
                )
            )
        )
    assert [line.body for line in said] == ["The editor said: Tuesday."]
    assert said[0].channel_id == direct
    assert said[0].reply_to_id == asked.id
