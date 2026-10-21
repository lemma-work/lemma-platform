"""E2E for the pod's acknowledgement to a contact nobody answers automatically.

With a contact's agents off, the pod may say one line it chose
(``config.contacts.auto_acknowledge``) -- once per open period: until a member
writes to them, or their channel is closed. It is the pod's agent's line on
their timeline, carried to the place they wrote from as a delivery, and it is
never said to a handle that asked the pod to stop.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.infrastructure.models.conversation import MessageModel
from app.modules.agent_surfaces.services.contact_acknowledgement import (
    acknowledge_contact,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    deliveries_of_line,
    member_writes,
    send_deliveries,
)
from app.modules.agent_surfaces.tests.e2e.test_contact_care_e2e import (
    _contact_writes_on,
)
from app.modules.agent_surfaces.tests.e2e.test_email_contacts_e2e import CUSTOMER
from app.modules.contacts.contracts import IdentityKind
from app.modules.contacts.contracts.outreach import SuppressionReason, opt_out

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

SAID = "Thanks, someone from {pod} will reply."


async def _acknowledging(client: AsyncClient, pod_id: str, text: str = SAID) -> str:
    """The pod says ``text`` to a waiting contact; its name, as said."""
    updated = await client.put(
        f"/pods/{pod_id}", json={"config": {"contacts": {"auto_acknowledge": text}}}
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["config"]["contacts"]["auto_acknowledge"] == text
    return updated.json()["name"]


async def _agents_off(client: AsyncClient, pod_id: str, channel_id: UUID) -> str:
    channel = f"/pods/{pod_id}/channels/{channel_id}"
    off = await client.patch(channel, json={"agents_answer": False})
    assert off.status_code == 200, off.text
    return channel


async def _acknowledgements(client: AsyncClient, channel: str, said: str) -> list[dict]:
    timeline = await client.get(f"{channel}/messages")
    assert timeline.status_code == 200, timeline.text
    return [line for line in timeline.json()["items"] if line["text"] == said]


async def test_a_waiting_contact_is_told_once_until_a_member_writes(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    channel_id, write, _runs = await _contact_writes_on(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    name = await _acknowledging(authenticated_client, pod_id)
    said = SAID.replace("{pod}", name)
    channel = await _agents_off(authenticated_client, pod_id, channel_id)

    await write("ack-2", "Can a person look at this?")
    [ack] = await _acknowledgements(authenticated_client, channel, said)
    assert ack["sender"]["kind"] == "AGENT"
    sent_before = len(message_store.get_all("RESEND"))
    await send_deliveries()
    [delivery] = await deliveries_of_line(ack["id"])
    assert delivery.status.value == "SENT", delivery.reason
    mailed = message_store.get_all("RESEND")[sent_before:]
    assert any(said in str(item) for item in mailed)
    assert any(CUSTOMER in str(item) for item in mailed)
    # Their conversation has it too: what a web chat shows them.
    db_session.expire_all()
    replies = await db_session.scalars(
        select(MessageModel.text).where(
            MessageModel.conversation_id.in_(
                select(ConversationModel.id).where(
                    ConversationModel.channel_id == channel_id
                )
            )
        )
    )
    assert said in list(replies)

    await write("ack-3", "Hello?")
    assert len(await _acknowledgements(authenticated_client, channel, said)) == 1

    await member_writes(authenticated_client, pod_id, str(channel_id), "On it, Dana.")
    await write("ack-4", "One more question.")
    assert len(await _acknowledgements(authenticated_client, channel, said)) == 2


async def test_closing_the_channel_starts_a_new_period(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    pod_id = test_pod["id"]
    channel_id, write, _runs = await _contact_writes_on(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    said = SAID.replace("{pod}", await _acknowledging(authenticated_client, pod_id))
    channel = await _agents_off(authenticated_client, pod_id, channel_id)

    await write("close-2", "Anyone?")
    closed = await authenticated_client.patch(channel, json={"status": "closed"})
    assert closed.status_code == 200, closed.text
    await write("close-3", "Me again, a week later.")

    assert len(await _acknowledgements(authenticated_client, channel, said)) == 2


async def test_nothing_is_said_with_the_agents_on_or_no_line_set(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    pod_id = test_pod["id"]
    channel_id, write, runs = await _contact_writes_on(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    channel = await _agents_off(authenticated_client, pod_id, channel_id)

    async def agent_lines() -> int:
        timeline = (await authenticated_client.get(f"{channel}/messages")).json()
        return sum(line["sender"]["kind"] == "AGENT" for line in timeline["items"])

    answered = await agent_lines()
    await write("none-2", "Anyone there?")
    assert await agent_lines() == answered

    said = SAID.replace("{pod}", await _acknowledging(authenticated_client, pod_id))
    on = await authenticated_client.patch(channel, json={"agents_answer": True})
    assert on.status_code == 200, on.text
    before = await runs()
    await write("none-3", "Back to the bot.")
    assert await runs() == before + 1
    assert await _acknowledgements(authenticated_client, channel, said) == []


async def test_a_handle_that_said_stop_is_not_acknowledged(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """Opt-out first: a suppressed address is not written to, and the period
    is not spent on it."""
    pod_id = test_pod["id"]
    channel_id, _write, _runs = await _contact_writes_on(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    said = SAID.replace("{pod}", await _acknowledging(authenticated_client, pod_id))
    channel = await _agents_off(authenticated_client, pod_id, channel_id)
    conversation_id = await db_session.scalar(
        select(ConversationModel.id).where(ConversationModel.channel_id == channel_id)
    )
    assert conversation_id is not None

    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await opt_out(
            uow,
            pod_id=UUID(pod_id),
            kind=IdentityKind.EMAIL,
            value=CUSTOMER,
            reason=SuppressionReason.BOUNCE,
        )
        assert (
            await acknowledge_contact(
                uow, pod_id=UUID(pod_id), conversation_id=conversation_id
            )
            is None
        )
        await uow.commit()

    assert await _acknowledgements(authenticated_client, channel, said) == []
