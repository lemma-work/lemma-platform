"""Heard at a place, or carried to one: two facts a line keeps apart.

A line a person said on a platform is *heard* there (``source_id``); a line
written in Lemma -- by a member or an agent -- is *carried* to the place it
goes (``target_source_id``) and planned a delivery row there. Only a heard
line says the place is alive (``channel_sources.last_inbound_at``): a member
writing to a contact opens no platform's window. And a line written in Lemma
is answered in Lemma, by the agents it names or that answer every line --
whether or not it is also carried to a mirrored group -- and the answer
follows it to that group's place.

Against Postgres, through the API a member uses, with the agent's run
finished by hand where one is started.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.core.infrastructure.work import WorkClass, WorkRegistry, facts
from app.core.infrastructure.work.testing import make_due, run_until_idle
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
)
from app.modules.agent.events.channel_answer_work import register_channel_answer_work
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.tests.e2e.lifecycle_helpers import runs
from app.modules.channels.contracts import (
    AccountKey,
    AgentAnswers,
    ContactsHome,
    GroupHome,
    NewLine,
    Place,
    Sender,
    Source,
    SourceKind,
    channel_by_id,
    post,
    source_for,
)
from app.modules.channels.contracts.deliveries import deliveries_of
from app.modules.channels.infrastructure.members import MemberRepository
from app.modules.channels.infrastructure.models import (
    ChannelLineAnswerModel,
    ChannelMessageModel,
    ChannelSourceModel,
)
from app.modules.channels.tests.e2e.channel_pod import Pod
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact

pytestmark = pytest.mark.e2e

PHONE = "+447700900456"


# ------------------------------------------------------------------- harness


async def _mirrored_group(db_session: AsyncSession, pod: Pod) -> Source:
    """A public Telegram group's channel, a contact heard in it a moment ago."""
    uow = SqlAlchemyUnitOfWork(db_session)
    source = await source_for(
        uow,
        pod_id=UUID(pod.id),
        place=Place(
            surface_id=None,
            platform="TELEGRAM",
            account=AccountKey(workspace="bot-carried"),
            kind=SourceKind.CHAT,
            external_channel_id="-200",
            external_user_id=None,
            title="Partners",
            is_private=False,
        ),
        home=GroupHome(title="Partners"),
    )
    tom = await open_contact(
        uow,
        pod_id=UUID(pod.id),
        kind=IdentityKind.TELEGRAM,
        value="777001",
        strength=IdentityStrength.CHANNEL,
        display_name="Tom",
    )
    await post(
        uow,
        channel_id=source.channel_id,
        line=NewLine(
            sender=Sender.contact(tom.id),
            text="Is the partner plan out?",
            source_id=source.id,
            external_message_id="tg-200-1",
        ),
    )
    await db_session.commit()
    return source


async def _whatsapp_contact(pod: Pod, *, heard_ago: timedelta) -> Source:
    """Ana, who last wrote on the pod's WhatsApp number ``heard_ago``."""
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        ana = await open_contact(
            uow,
            pod_id=UUID(pod.id),
            kind=IdentityKind.PHONE,
            value=PHONE,
            strength=IdentityStrength.CHANNEL,
            display_name="Ana",
        )
        source = await source_for(
            uow,
            pod_id=UUID(pod.id),
            place=Place(
                surface_id=None,
                platform="WHATSAPP",
                account=AccountKey(workspace="number:+15550002222"),
                kind=SourceKind.PERSON,
                external_channel_id=None,
                external_user_id=PHONE.lstrip("+"),
            ),
            home=ContactsHome(contact_id=ana.id),
        )
        await post(
            uow,
            channel_id=source.channel_id,
            line=NewLine(
                sender=Sender.contact(ana.id), text="Hello?", source_id=source.id
            ),
        )
        await session.execute(
            update(ChannelSourceModel)
            .where(ChannelSourceModel.id == source.id)
            .values(last_inbound_at=datetime.now(timezone.utc) - heard_ago)
        )
        await uow.commit()
    return source


async def _line(message_id: str | UUID) -> ChannelMessageModel:
    async with async_session_maker() as session:
        row = await session.get(ChannelMessageModel, UUID(str(message_id)))
    assert row is not None
    return row


async def _answering(message_id: str | UUID) -> UUID | None:
    """The conversation an agent answers the line in, if one does."""
    async with async_session_maker() as session:
        return await session.scalar(
            select(ChannelLineAnswerModel.conversation_id).where(
                ChannelLineAnswerModel.message_id == UUID(str(message_id))
            )
        )


async def _last_inbound(source_id: UUID) -> datetime | None:
    async with async_session_maker() as session:
        return await session.scalar(
            select(ChannelSourceModel.last_inbound_at).where(
                ChannelSourceModel.id == source_id
            )
        )


async def _place_rows(message_id: str | UUID) -> set[UUID]:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        rows = await deliveries_of(uow, [UUID(str(message_id))])
    return {row.source_id for row in rows if row.source_id is not None}


async def _drain(*classes: WorkClass) -> None:
    registry = WorkRegistry()
    register_channel_answer_work(registry)
    await make_due(async_session_maker)
    with suppress_agent_run_enqueue():
        await run_until_idle(
            async_session_maker, registry=registry, classes=list(classes)
        )


def _agent(agent_id: str | UUID) -> dict[str, str]:
    return {"type": "mention", "kind": "AGENT", "id": str(agent_id), "name": "Lem"}


# ------------------------------------------------------- carried, never heard


async def test_a_member_line_carried_to_a_mirrored_group_plans_its_place_row(
    pod: Pod, db_session: AsyncSession
):
    place = await _mirrored_group(db_session, pod)
    heard_at = await _last_inbound(place.id)

    said = await pod.client().post(
        f"{pod.base}/{place.channel_id}/messages", json={"text": "Next week."}
    )

    assert said.status_code == 201, said.text
    # The reader is shown where it went, as before.
    assert said.json()["source_id"] == str(place.id)
    row = await _line(said.json()["id"])
    assert (row.source_id, row.target_source_id) == (None, place.id)
    assert await _place_rows(row.id) == {place.id}
    # Writing there from Lemma is not hearing from there.
    assert await _last_inbound(place.id) == heard_at


async def test_a_member_line_to_a_contact_place_plans_its_row_and_opens_no_window(
    pod: Pod,
):
    """Ana last wrote a day and an hour ago. An editor's line to her goes to
    her WhatsApp place -- and leaves the window closed, so outreach there is
    still refused rather than sent into a window Meta has shut."""
    place = await _whatsapp_contact(pod, heard_ago=timedelta(hours=25))
    heard_at = await _last_inbound(place.id)
    editor = pod.client(pod.editor)

    said = await editor.post(
        f"{pod.base}/{place.channel_id}/messages",
        json={"text": "Sorry for the wait.", "source_id": str(place.id)},
    )

    assert said.status_code == 201, said.text
    assert await _place_rows(said.json()["id"]) == {place.id}
    assert await _last_inbound(place.id) == heard_at
    channel = (await editor.get(f"{pod.base}/{place.channel_id}")).json()
    contact_id = channel["person"]["id"]
    again = await editor.post(
        f"/pods/{pod.id}/contacts/{contact_id}/outreach",
        json={"handle": PHONE, "text": "Are you still there?"},
    )
    assert again.status_code == 422, again.text
    assert "24 hours" in again.json()["message"]


# --------------------------------------------- answered in Lemma, then carried


@pytest.mark.drives_work
async def test_an_agent_answer_to_a_carried_group_line_follows_it_to_the_place(
    pod: Pod, db_session: AsyncSession
):
    """``@Lem`` in Lemma, in a group mirrored from Telegram: the pod's agent
    answers it -- a line heard on Telegram would be answered there, but this
    one was written here -- and its answer goes where the question went."""
    place = await _mirrored_group(db_session, pod)
    owner = UUID(pod.owner["id"])

    said = await pod.client().post(
        f"{pod.base}/{place.channel_id}/messages",
        json={"text": "@Lem when is the launch?", "parts": [_agent(pod.id)]},
    )
    assert said.status_code == 201, said.text
    await _drain(WorkClass.INTERACTIVE)

    conversation_id = await _answering(said.json()["id"])
    assert conversation_id is not None, "no agent answers a line written in Lemma"
    [run] = await runs(conversation_id)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ConversationRepository(uow).finish_agent_run(
            agent_run_id=run.id,
            status=AgentRunStatus.COMPLETED,
            conversation_status=ConversationStatus.COMPLETED,
            output_data="On the 4th.",
        )
        await facts.emit(
            uow,
            AgentRunFinished(
                run_id=run.id,
                conversation_id=conversation_id,
                pod_id=UUID(pod.id),
                user_id=owner,
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
    assert answer.body == "On the 4th."
    # A Telegram group reads as a chat: the answer follows the line in the
    # main timeline, pointing at it, rather than opening a thread under it.
    assert answer.thread_root_id is None
    assert answer.reply_to_id == UUID(said.json()["id"])
    assert (answer.source_id, answer.target_source_id) == (None, place.id)
    assert await _place_rows(answer.id) == {place.id}


@pytest.mark.drives_work
async def test_an_agent_answering_every_line_answers_one_carried_to_a_group(
    pod: Pod, db_session: AsyncSession
):
    place = await _mirrored_group(db_session, pod)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        channel = await channel_by_id(uow, place.channel_id)
        assert channel is not None
        await MemberRepository(uow.session).add_agent(
            channel, UUID(pod.id), answers=AgentAnswers.EVERY_LINE
        )
        await uow.commit()

    said = await pod.client().post(
        f"{pod.base}/{place.channel_id}/messages", json={"text": "Status, anyone?"}
    )
    assert said.status_code == 201, said.text
    await _drain(WorkClass.INTERACTIVE)

    assert await _answering(said.json()["id"]) is not None
