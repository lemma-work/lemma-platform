"""Which agent answers a line does not depend on where it was typed.

A group mirrored from Telegram is a channel members also write to in Lemma.
Its bot is the pod's agent; a second agent sits in the channel answering every
line. The same words get the same agents either way: typed in Lemma, both
are started here; heard on Telegram, the bot answers at its door
(``agent_surfaces``) and the other agent is started here -- and answers at
the place the line was heard, as the bot would.

Against Postgres, through the API a member uses and the contract a surface
writes heard lines with, with the agent's run finished by hand.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.work import WorkClass, WorkRegistry, facts
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.domain.value_objects import AgentRunStatus, ConversationStatus
from app.modules.agent.events.channel_answer_work import register_channel_answer_work
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.tests.e2e.lifecycle_helpers import runs
from app.modules.channels.contracts import (
    AccountKey,
    GroupHome,
    NewLine,
    Place,
    Sender,
    Source,
    SourceKind,
    post,
    source_for,
)
from app.modules.channels.contracts.deliveries import deliveries_of
from app.modules.channels.infrastructure.models import (
    ChannelLineAnswerModel,
    ChannelMessageModel,
)
from app.modules.channels.tests.e2e.channel_pod import Pod

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


async def _mirrored_group(
    db_session: AsyncSession, pod: Pod, *, chat: str = "-300"
) -> Source:
    source = await source_for(
        SqlAlchemyUnitOfWork(db_session),
        pod_id=UUID(pod.id),
        place=Place(
            surface_id=None,
            platform="TELEGRAM",
            account=AccountKey(workspace="bot-who-answers"),
            kind=SourceKind.CHAT,
            external_channel_id=chat,
            external_user_id=None,
            title="Launch",
            is_private=False,
        ),
        home=GroupHome(title="Launch"),
    )
    await db_session.commit()
    return source


async def _seat_every_line(pod: Pod, channel_id: UUID, agent_id: str) -> None:
    seated = await pod.client().post(
        f"{pod.base}/{channel_id}/members",
        json={"agents": [{"id": agent_id, "answers": "EVERY_LINE"}]},
    )
    assert seated.status_code == 200, seated.text


async def _heard(
    pod: Pod,
    place: Source,
    *,
    text: str,
    message_id: str,
    mentioned_agents: tuple[UUID, ...] = (),
) -> UUID:
    """A member's message heard on Telegram, written as the group log writes
    it: through the bot, the pod's agent."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        line = await post(
            uow,
            channel_id=place.channel_id,
            line=NewLine(
                sender=Sender.member(UUID(pod.owner["id"])),
                text=text,
                source_id=place.id,
                external_message_id=message_id,
            ),
            mentioned_agents=mentioned_agents,
            door_agent_id=UUID(pod.id),
        )
        await uow.commit()
    assert line is not None
    return line.id


async def _typed(pod: Pod, place: Source, *, text: str, parts=()) -> UUID:
    said = await pod.client().post(
        f"{pod.base}/{place.channel_id}/messages",
        json={"text": text, "parts": list(parts)},
    )
    assert said.status_code == 201, said.text
    return UUID(said.json()["id"])


async def _started(message_id: UUID) -> dict[UUID, UUID]:
    """The agents started for the line here, and the conversation each
    answers it in."""
    async with async_session_maker() as session:
        rows = await session.execute(
            select(
                ChannelLineAnswerModel.agent_id, ChannelLineAnswerModel.conversation_id
            ).where(ChannelLineAnswerModel.message_id == message_id)
        )
    return {row.agent_id: row.conversation_id for row in rows}


async def _drain(*classes: WorkClass) -> None:
    registry = WorkRegistry()
    register_channel_answer_work(registry)
    await make_due(async_session_maker)
    with suppress_agent_run_enqueue():
        await run_until_idle(
            async_session_maker, registry=registry, classes=list(classes)
        )


def _naming(agent_id: str) -> dict[str, str]:
    return {"type": "mention", "kind": "AGENT", "id": agent_id, "name": "Lem"}


async def test_a_line_is_answered_by_the_same_agents_typed_in_lemma_or_heard_on_the_platform(
    pod: Pod, db_session: AsyncSession
):
    """Four groups, so each line is the first its agents answer there: a group
    line said while an agent works on another waits for it, wherever said."""
    reviewer = await pod.scenario.create_agent(name="reviewer")
    bot, other = UUID(pod.id), UUID(reviewer["id"])
    started: dict[str, set[UUID]] = {}
    for name, chat in (
        ("typed", "-301"),
        ("heard", "-302"),
        ("typed_to_bot", "-303"),
        ("heard_to_bot", "-304"),
    ):
        place = await _mirrored_group(db_session, pod, chat=chat)
        await _seat_every_line(pod, place.channel_id, reviewer["id"])
        if name == "typed":
            line = await _typed(pod, place, text="Who owns the launch post?")
        elif name == "heard":
            line = await _heard(pod, place, text="Who owns it?", message_id="tg-1")
        elif name == "typed_to_bot":
            line = await _typed(
                pod, place, text="@Lem and the date?", parts=[_naming(pod.id)]
            )
        else:
            line = await _heard(
                pod,
                place,
                text="@Lem and the budget?",
                message_id="tg-2",
                mentioned_agents=(bot,),
            )
        await _drain(WorkClass.INTERACTIVE)
        started[name] = set(await _started(line))

    # Nobody named: the agent answering every line, wherever it was typed.
    assert started["typed"] == started["heard"] == {other}
    # The bot named: in Lemma it is started here; on Telegram it answers at
    # its door, so here it is only the other agent -- the same two agents.
    assert started["typed_to_bot"] == {bot, other}
    assert started["heard_to_bot"] == {other}


async def test_an_agent_answering_a_line_heard_on_the_platform_answers_there(
    pod: Pod, db_session: AsyncSession
):
    place = await _mirrored_group(db_session, pod)
    reviewer = await pod.scenario.create_agent(name="reviewer")
    await _seat_every_line(pod, place.channel_id, reviewer["id"])
    heard = await _heard(
        pod, place, text="Is the launch still Thursday?", message_id="tg-300-3"
    )
    await _drain(WorkClass.INTERACTIVE)
    conversation_id = (await _started(heard))[UUID(reviewer["id"])]

    [run] = await runs(conversation_id)
    assert run.to_entity().state.asked_at_source == place.id
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await Runs(uow).finish_agent_run(
            agent_run_id=run.id,
            status=AgentRunStatus.COMPLETED,
            conversation_status=ConversationStatus.COMPLETED,
            output_data="Yes, Thursday.",
        )
        await facts.emit(
            uow,
            AgentRunFinished(
                run_id=run.id,
                conversation_id=conversation_id,
                pod_id=UUID(pod.id),
                user_id=UUID(pod.owner["id"]),
                status="COMPLETED",
                conversation_status="COMPLETED",
                origin="chat",
            ),
        )
        await uow.commit()
    await _drain(WorkClass.EVENTS)

    async with async_session_maker() as session:
        [answer] = await session.scalars(
            select(ChannelMessageModel).where(
                ChannelMessageModel.agent_run_id == run.id
            )
        )
        state = await session.scalar(
            select(ChannelLineAnswerModel.state).where(
                ChannelLineAnswerModel.message_id == heard
            )
        )
    assert answer.body == "Yes, Thursday."
    assert answer.sender_agent_id == UUID(reviewer["id"])
    # At the place the line was heard, with its delivery there.
    assert answer.target_source_id == place.id
    async with create_uow_from_session_maker(async_session_maker) as uow:
        sends = await deliveries_of(uow, [answer.id])
    assert {send.source_id for send in sends if send.source_id} == {place.id}
    assert state == "ANSWERED"
