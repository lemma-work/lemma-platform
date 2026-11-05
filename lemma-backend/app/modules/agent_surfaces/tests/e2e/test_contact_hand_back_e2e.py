"""E2E for the hand-back window of a member assigned to a contact.

A member who writes to a contact holds them (``test_contact_care_e2e``); so
does one assigned to them, before writing a word: the window runs from being
assigned. Past it, the contact's next message is the agent's again, and the
member is told in their own channel.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent_surfaces.services import contact_hold
from app.modules.agent_surfaces.tests.e2e.surface_work_support import direct_lines
from app.modules.agent_surfaces.tests.e2e.test_contact_care_e2e import (
    _contact_writes_on,
)
from app.modules.channels.infrastructure.models import ChannelCaseModel

pytestmark = pytest.mark.e2e

HANDED_BACK = "the agent answers them again"


async def test_an_assigned_member_holds_the_contact_until_the_window_passes(
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
    channel = f"/pods/{pod_id}/channels/{channel_id}"
    assigned = await authenticated_client.patch(
        channel, json={"assignee": fixed_test_user["id"], "hand_back_after_hours": 2}
    )
    assert assigned.status_code == 200, assigned.text
    assert assigned.json()["assignee"]["id"] == fixed_test_user["id"]
    before = await runs()

    # Assigned, not yet written: the member holds them.
    await write("hold-2", "Is anybody looking at this?")
    assert await runs() == before
    assert (await authenticated_client.get(channel)).json()["assignee"] is not None

    async def told() -> bool:
        lines = await direct_lines(
            db_session, pod_id=pod_id, user_id=fixed_test_user["id"]
        )
        return any(HANDED_BACK in line.body for line in lines)

    assert not await told()

    # Assigned three hours ago, and silent since: past the window.
    await db_session.execute(
        update(ChannelCaseModel)
        .where(ChannelCaseModel.channel_id == channel_id)
        .values(assigned_at=datetime.now(timezone.utc) - timedelta(hours=3))
    )
    await db_session.commit()
    await write("hold-3", "Hello?")

    assert await runs() == before + 1
    assert (await authenticated_client.get(channel)).json()["assignee"] is None
    assert await told()


async def test_two_messages_arriving_together_hand_back_once(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """Both turns read the assignee as gone quiet before either lets go: the
    hand-back is one conditional write, so one lets go and tells them, and
    the other finds nothing to hand back."""
    pod_id = test_pod["id"]
    channel_id, _write, _runs = await _contact_writes_on(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    assigned = await authenticated_client.patch(
        f"/pods/{pod_id}/channels/{channel_id}",
        json={"assignee": fixed_test_user["id"], "hand_back_after_hours": 2},
    )
    assert assigned.status_code == 200, assigned.text
    await db_session.execute(
        update(ChannelCaseModel)
        .where(ChannelCaseModel.channel_id == channel_id)
        .values(assigned_at=datetime.now(timezone.utc) - timedelta(hours=3))
    )
    await db_session.commit()
    conversation_id = await db_session.scalar(
        select(ConversationModel.id).where(ConversationModel.channel_id == channel_id)
    )
    assert conversation_id is not None

    # Each turn reads the channel, then waits until the other has read it too.
    read = asyncio.Barrier(2)
    reading = contact_hold.channel_by_id
    first_read: set[asyncio.Task] = set()

    async def read_together(uow, channel):
        found = await reading(uow, channel)
        task = asyncio.current_task()
        if task not in first_read:
            first_read.add(task)
            await read.wait()
        return found

    monkeypatch.setattr(contact_hold, "channel_by_id", read_together)

    async def turn():
        async with async_session_maker() as session:
            uow = SqlAlchemyUnitOfWork(session)
            hold = await contact_hold.held_by_a_member(uow, conversation_id)
            await uow.commit()
        return hold

    holds = await asyncio.gather(turn(), turn())

    assert holds == [None, None]
    lines = await direct_lines(db_session, pod_id=pod_id, user_id=fixed_test_user["id"])
    assert sum(HANDED_BACK in line.body for line in lines) == 1
