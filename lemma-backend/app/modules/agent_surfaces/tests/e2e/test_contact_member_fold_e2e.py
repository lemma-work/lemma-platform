"""A guest who proved to be a member becomes them in the pod's groups -- end to end.

A member whose Telegram account Lemma did not know spoke in a group as a
contact. Once the account is proved theirs, each test pins one rule:

* the contact that was only that account is the member: it holds nothing, is
  not listed, and its lines in the group are the member's;
* what the bot answered it keeps saying it was made from what is Public;
* a contact with other handles keeps them and stays a contact: only the
  account is taken, and the pod's administrators are told;
* a pod the person is not a member of keeps its contact as it was.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.services.proved_account_folds import (
    fold_proved_account,
)
from app.modules.agent_surfaces.tests.e2e.group_timeline import (
    a_stranger,
    bot_for,
    say,
    timeline,
)
from app.modules.agent_surfaces.tests.e2e.test_outsider_isolation_e2e import (
    _group_with_owner,
)
from app.modules.channels.contracts import Principal, Sender, SenderKind
from app.modules.channels.infrastructure.contact_channels import ContactChannels
from app.modules.channels.infrastructure.models import ChannelMemberModel
from app.modules.contacts.contracts import (
    IdentityKind,
    IdentityStrength,
    contact_handles,
    prove_handle,
)
from app.modules.contacts.infrastructure.models import ContactModel

pytestmark = pytest.mark.e2e

GUEST_ACCOUNT = "7000303"


async def _contact_row(db_session: AsyncSession, contact_id: UUID) -> ContactModel:
    db_session.expire_all()
    row = await db_session.get(ContactModel, contact_id)
    assert row is not None
    return row


async def test_a_guest_who_proved_to_be_a_member_is_them_in_the_group(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    _, group, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    guest = await a_stranger(db_session, group, telegram_id=GUEST_ACCOUNT, name="Anu")
    assert guest.contact_id is not None
    await say(
        db_session,
        group,
        "We are six in total.",
        sender=Sender.contact(guest.contact_id, display_name="Anu"),
    )
    await say(
        db_session,
        group,
        "Noted, six people.",
        sender=bot_for(guest, agent_id=UUID(scenario.pod_id)),
    )
    await db_session.commit()

    uow = SqlAlchemyUnitOfWork(db_session)
    await fold_proved_account(uow, user_id=owner, external_user_id=GUEST_ACCOUNT)
    # What ContactBecameMember's FAST item does, run here in the same session.
    moved = await ContactChannels(db_session).became_member(
        pod_id=UUID(scenario.pod_id), contact_id=guest.contact_id, user_id=owner
    )
    await db_session.commit()

    assert moved == 1
    row = await _contact_row(db_session, guest.contact_id)
    assert row.merged_into_user_id == owner and row.merged_into_id is None
    assert (
        await contact_handles(
            uow, pod_id=UUID(scenario.pod_id), contact_id=guest.contact_id
        )
        == []
    )
    said = {line.text: line for line in await timeline(db_session, group)}
    theirs = said["We are six in total."].sender
    assert (theirs.kind, theirs.user_id) == (SenderKind.MEMBER, owner)
    # Answered as somebody outside, from Public: it still says so.
    answer = said["Noted, six people."].sender
    assert answer.on_behalf_of == Principal.contact(guest.contact_id)
    assert not answer.used_a_members_access
    seats = (
        await db_session.scalars(
            select(ChannelMemberModel).where(
                ChannelMemberModel.contact_id == guest.contact_id
            )
        )
    ).all()
    assert seats == []

    listed = await scenario.owner_client.get(f"/pods/{scenario.pod_id}/contacts")
    assert listed.status_code == 200, listed.text
    assert str(guest.contact_id) not in {c["id"] for c in listed.json()["items"]}


async def test_a_guest_with_other_handles_keeps_them_and_an_admin_is_told(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    _, group, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    guest = await a_stranger(db_session, group, telegram_id=GUEST_ACCOUNT, name="Anu")
    assert guest.contact_id is not None
    uow = SqlAlchemyUnitOfWork(db_session)
    await prove_handle(
        uow,
        pod_id=UUID(scenario.pod_id),
        claimant_id=guest.contact_id,
        kind=IdentityKind.EMAIL,
        value="anu@example.com",
        strength=IdentityStrength.CODE,
    )
    await db_session.commit()

    await fold_proved_account(uow, user_id=owner, external_user_id=GUEST_ACCOUNT)
    await db_session.commit()

    row = await _contact_row(db_session, guest.contact_id)
    assert row.merged_into_user_id is None
    kept = await contact_handles(
        uow, pod_id=UUID(scenario.pod_id), contact_id=guest.contact_id
    )
    assert [(h.kind, h.value) for h in kept] == [
        (IdentityKind.EMAIL, "anu@example.com")
    ]
    mine = await scenario.owner_client.get(f"/pods/{scenario.pod_id}/channels/me")
    assert mine.status_code == 200, mine.text
    page = await scenario.owner_client.get(
        f"/pods/{scenario.pod_id}/channels/{mine.json()['id']}/messages"
    )
    assert page.status_code == 200, page.text
    assert any(
        "was taken from that contact" in (line.get("text") or "")
        for line in page.json()["items"]
    )


async def test_a_pod_the_person_is_not_in_keeps_its_contact(
    scenario, db_session: AsyncSession, fake_telegram, monkeypatch
):
    _, group, owner = await _group_with_owner(
        scenario, db_session, fake_telegram, monkeypatch
    )
    guest = await a_stranger(db_session, group, telegram_id=GUEST_ACCOUNT, name="Anu")
    assert guest.contact_id is not None
    await db_session.commit()

    await fold_proved_account(
        SqlAlchemyUnitOfWork(db_session),
        user_id=owner,
        external_user_id=GUEST_ACCOUNT,
        pods=[],
    )
    await db_session.commit()

    row = await _contact_row(db_session, guest.contact_id)
    assert row.merged_into_user_id is None
