"""E2E for a contact nobody is answering, and who is told about it.

Past the contacts cap no run answers a contact (``outside_cap``): the member
who looks after the conversation is told, pushed, since nobody else will
answer. A keeper who cannot open a contact's channel (no ``contact.read``) is
not pointed at one: they are told how to get access, and the pod's
administrators -- who can open it -- are told it is theirs. The contact is
promised a reply only when somebody who can open their channel was told.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.models.conversation import MessageModel
from app.modules.agent_surfaces.services.outside_cap import hand_to_a_person
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    deliveries_of_line,
    direct_lines,
)
from app.modules.agent_surfaces.tests.e2e.test_contact_care_e2e import _dana_writes
from app.modules.agent_surfaces.tests.e2e.test_email_contacts_e2e import _email_bot
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    invite_org_member,
    signup_user,
)

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


async def _a_viewer(
    owner_client: AsyncClient, async_client: AsyncClient, *, org_id: str, pod_id: str
) -> dict:
    """A member who may not see the pod's contacts."""
    user = await signup_user(async_client, "keeper-viewer")
    member = await invite_org_member(
        owner_client, async_client, org_id=org_id, user=user
    )
    await add_pod_member(
        owner_client,
        pod_id=pod_id,
        organization_member_id=member["id"],
        role="POD_VIEWER",
    )
    return user


async def _held(conversation_id: UUID, pod_id: str, owner_id: UUID) -> str | None:
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        held = await hand_to_a_person(
            uow,
            pod_id=UUID(pod_id),
            conversation_id=conversation_id,
            owner_id=owner_id,
            text="Anyone there?",
            metadata={},
            tell=True,
        )
        await uow.commit()
    return held.reply


async def _promised(conversation_id: UUID) -> int:
    async with async_session_maker() as session:
        texts = (
            await session.scalars(
                select(MessageModel.text).where(
                    MessageModel.conversation_id == conversation_id
                )
            )
        ).all()
    return sum("will reply here" in (text or "") for text in texts)


async def test_a_keeper_who_cannot_open_contacts_is_told_how_and_admins_answer(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    pod_id = test_pod["id"]
    admin = UUID(fixed_test_user["id"])
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    dana = await _dana_writes(db_session, address, "keeper-1")
    viewer = await _a_viewer(
        authenticated_client, async_client, org_id=fixed_test_org["id"], pod_id=pod_id
    )
    keeper = UUID(viewer["id"])

    reply = await _held(dana.conversation_id, pod_id, keeper)

    # The contact is promised a person -- an administrator was told -- as the
    # pod's line carried to her place, not a reply for the caller to send.
    assert reply is None
    assert await _promised(dana.conversation_id) == 1
    [note] = await direct_lines(db_session, pod_id=pod_id, user_id=keeper)
    assert "cannot open contacts' channels" in note.body
    assert "Their channel is in Contacts" not in note.body
    note_id = note.id
    [admin_note] = await direct_lines(db_session, pod_id=pod_id, user_id=admin)
    assert "it is yours" in admin_note.body
    # Both pushed: somebody outside is waiting, and nobody else is answering.
    for line_id, user in ((note_id, keeper), (admin_note.id, admin)):
        assert [d.recipient_user_id for d in await deliveries_of_line(line_id)] == [
            user
        ]


async def test_a_keeper_who_can_open_contacts_is_pointed_at_the_channel(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    pod_id = test_pod["id"]
    keeper = UUID(fixed_test_user["id"])
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    dana = await _dana_writes(db_session, address, "keeper-2")

    await _held(dana.conversation_id, pod_id, keeper)

    [note] = await direct_lines(db_session, pod_id=pod_id, user_id=keeper)
    assert "their channel is in Contacts" in note.body
    [push] = await deliveries_of_line(note.id)
    assert push.recipient_user_id == keeper
