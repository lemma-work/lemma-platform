"""E2E for a member writing to a contact first (``POST …/contacts/{c}/outreach``).

The line is the member's, in the contact's channel, at the place the handle
is reached through -- opened the first time for email -- and is carried there
as a delivery. What may be sent is the outreach policy's: email any time,
WhatsApp only within 24 hours of the contact's last message at that place,
never a handle that asked the pod to stop, nothing a bot cannot start.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    deliveries_of_line,
    send_deliveries,
)
from app.modules.agent_surfaces.tests.e2e.test_contact_care_e2e import (
    _dana,
    _dana_writes,
    _member,
)
from app.modules.agent_surfaces.tests.e2e.test_email_contacts_e2e import (
    CUSTOMER,
    _email_bot,
)
from app.modules.channels.contracts import (
    AccountKey,
    ContactsHome,
    Place,
    SourceKind,
    source_for,
)
from app.modules.channels.infrastructure.models import ChannelSourceModel
from app.modules.contacts.contracts import (
    IdentityKind,
    IdentityStrength,
    open_contact,
    prove_handle,
)
from app.modules.contacts.contracts.outreach import SuppressionReason, opt_out
from app.modules.test_support.e2e_authz import auth_headers

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]

PHONE = "+447700900123"


async def _reach(
    client: AsyncClient, pod_id: str, contact_id: str, handle: str, text: str, **body
):
    return await client.post(
        f"/pods/{pod_id}/contacts/{contact_id}/outreach",
        json={"handle": handle, "text": text, **body},
    )


async def _timeline(client: AsyncClient, pod_id: str, channel_id: str) -> list[dict]:
    listed = await client.get(f"/pods/{pod_id}/channels/{channel_id}/messages")
    assert listed.status_code == 200, listed.text
    return listed.json()["items"]


async def _contact(pod_id: str, kind: IdentityKind, value: str, name: str) -> UUID:
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        contact = await open_contact(
            uow,
            pod_id=UUID(pod_id),
            kind=kind,
            value=value,
            strength=IdentityStrength.CHANNEL,
            display_name=name,
        )
        await uow.commit()
    return contact.id


async def _whatsapp_place(pod_id: str, contact_id: UUID, *, heard_ago: timedelta):
    """Their WhatsApp chat with the pod's number, last heard ``heard_ago``."""
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        source = await source_for(
            uow,
            pod_id=UUID(pod_id),
            place=Place(
                surface_id=None,
                platform="WHATSAPP",
                account=AccountKey(workspace="number:+15550001111"),
                kind=SourceKind.PERSON,
                external_channel_id=None,
                # WhatsApp names them by their number without its ``+``.
                external_user_id=PHONE.lstrip("+"),
            ),
            home=ContactsHome(contact_id=contact_id),
        )
        await session.execute(
            update(ChannelSourceModel)
            .where(ChannelSourceModel.id == source.id)
            .values(last_inbound_at=datetime.now(timezone.utc) - heard_ago)
        )
        await uow.commit()
    return source


async def test_an_email_contact_is_written_to_where_they_wrote(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    await _dana_writes(db_session, address, "outreach-1")
    dana = await _dana(authenticated_client, pod_id)
    text = f"Your replacement ships Monday [{uuid4().hex[:6]}]"

    reached = await _reach(authenticated_client, pod_id, dana, CUSTOMER.upper(), text)

    assert reached.status_code == 201, reached.text
    line, delivery = reached.json()["line"], reached.json()["delivery"]
    assert line["text"] == text
    assert delivery["status"] == "PENDING"
    assert delivery["source_id"] == line["source_id"]
    timeline = await _timeline(authenticated_client, pod_id, line["channel_id"])
    [written] = [item for item in timeline if item["id"] == line["id"]]
    assert written["sender"] == {
        "kind": "MEMBER",
        "id": fixed_test_user["id"],
        "name": written["sender"]["name"],
    }
    sent_before = len(message_store.get_all("RESEND"))
    await send_deliveries()
    [row] = await deliveries_of_line(line["id"])
    assert row.status.value == "SENT", row.reason
    assert any(
        text in str(item) for item in message_store.get_all("RESEND")[sent_before:]
    )

    # A retry with the same id is the same line, and sends nothing new.
    again = await _reach(
        authenticated_client, pod_id, dana, CUSTOMER, text, client_message_id="x-1"
    )
    retried = await _reach(
        authenticated_client, pod_id, dana, CUSTOMER, text, client_message_id="x-1"
    )
    assert again.status_code == retried.status_code == 201, retried.text
    assert again.json()["line"]["id"] == retried.json()["line"]["id"]

    # So is one with the same Idempotency-Key.
    once = f"outreach-{dana}"
    keyed = [
        await authenticated_client.post(
            f"/pods/{pod_id}/contacts/{dana}/outreach",
            json={"handle": CUSTOMER, "text": "keyed"},
            headers={"Idempotency-Key": once},
        )
        for _ in range(2)
    ]
    assert [r.status_code for r in keyed] == [201, 201], keyed[1].text
    assert keyed[0].json()["line"]["id"] == keyed[1].json()["line"]["id"]


async def test_an_email_address_nobody_wrote_from_opens_a_place(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """Email-first: the contact never wrote; the line opens their place on
    the pod's mailbox and starts the thread."""
    pod_id = test_pod["id"]
    await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    lead = "lead@prospect.example"
    contact_id = await _contact(pod_id, IdentityKind.EMAIL, lead, "Lee")
    text = f"Following up on your enquiry [{uuid4().hex[:6]}]"

    reached = await _reach(authenticated_client, pod_id, str(contact_id), lead, text)

    assert reached.status_code == 201, reached.text
    line = reached.json()["line"]
    channel = await authenticated_client.get(
        f"/pods/{pod_id}/channels/{line['channel_id']}"
    )
    assert channel.json()["kind"] == "CONTACT"
    assert any(
        source["id"] == line["source_id"] and source["platform"] == "RESEND"
        for source in channel.json()["source_places"]
    )
    sent_before = len(message_store.get_all("RESEND"))
    await send_deliveries()
    [row] = await deliveries_of_line(line["id"])
    assert row.status.value == "SENT", row.reason
    mailed = message_store.get_all("RESEND")[sent_before:]
    assert any(text in str(item) and lead in str(item) for item in mailed)


async def test_whatsapp_only_within_a_day_of_their_last_message(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    contact_id = await _contact(pod_id, IdentityKind.PHONE, PHONE, "Ana")
    await _whatsapp_place(pod_id, contact_id, heard_ago=timedelta(hours=30))

    late = await _reach(authenticated_client, pod_id, str(contact_id), PHONE, "Hi Ana")
    assert late.status_code == 422, late.text
    assert "24 hours" in late.json()["message"]
    contact_channel = await authenticated_client.get(
        f"/pods/{pod_id}/channels", params={"kind": "CONTACT"}
    )
    [channel] = [
        item
        for item in contact_channel.json()["items"]
        if (item.get("person") or {}).get("id") == str(contact_id)
    ]
    assert await _timeline(authenticated_client, pod_id, channel["id"]) == []

    source = await _whatsapp_place(pod_id, contact_id, heard_ago=timedelta(hours=2))
    soon = await _reach(
        authenticated_client, pod_id, str(contact_id), "+44 7700 900123", "Hi Ana"
    )
    assert soon.status_code == 201, soon.text
    assert soon.json()["delivery"]["source_id"] == str(source.id)


async def test_refused_handles_post_nothing(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    async_client: AsyncClient,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    fake_resend,
    monkeypatch,
):
    pod_id = test_pod["id"]
    await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    stopped = "gone@prospect.example"
    contact_id = str(await _contact(pod_id, IdentityKind.EMAIL, stopped, "Gone"))
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await opt_out(
            uow,
            pod_id=UUID(pod_id),
            kind=IdentityKind.EMAIL,
            value=stopped,
            reason=SuppressionReason.STOP,
        )
        await uow.commit()

    suppressed = await _reach(authenticated_client, pod_id, contact_id, stopped, "Hi")
    assert suppressed.status_code == 409, suppressed.text

    not_theirs = await _reach(
        authenticated_client, pod_id, contact_id, "other@prospect.example", "Hi"
    )
    assert not_theirs.status_code == 422, not_theirs.text

    phone_only = str(await _contact(pod_id, IdentityKind.PHONE, "+15557654321", "Pat"))
    never_wrote = await _reach(
        authenticated_client, pod_id, phone_only, "+15557654321", "Hi"
    )
    assert never_wrote.status_code == 422, never_wrote.text

    missing = await _reach(authenticated_client, pod_id, str(uuid4()), stopped, "Hi")
    assert missing.status_code == 404, missing.text

    viewer = await _member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_USER",
        prefix="outreach-user",
    )
    as_viewer = await async_client.post(
        f"/pods/{pod_id}/contacts/{contact_id}/outreach",
        json={"handle": stopped, "text": "Hi"},
        headers=auth_headers(viewer),
    )
    assert as_viewer.status_code == 403, as_viewer.text


async def test_a_key_used_for_another_of_their_handles_is_a_conflict(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """The same client id, retried at a second handle of theirs, is not the
    same request: refused as a key reused for another line, and nothing is
    written -- not a 500."""
    pod_id = test_pod["id"]
    await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    work, home = "lee@prospect.example", "lee@home.example"
    contact_id = await _contact(pod_id, IdentityKind.EMAIL, work, "Lee")
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await prove_handle(
            uow,
            pod_id=UUID(pod_id),
            claimant_id=contact_id,
            kind=IdentityKind.EMAIL,
            value=home,
            strength=IdentityStrength.CHANNEL,
        )
        await uow.commit()

    first = await _reach(
        authenticated_client,
        pod_id,
        str(contact_id),
        work,
        "Hi",
        client_message_id="k-1",
    )
    assert first.status_code == 201, first.text
    elsewhere = await _reach(
        authenticated_client,
        pod_id,
        str(contact_id),
        home,
        "Hi",
        client_message_id="k-1",
    )

    assert elsewhere.status_code == 422, elsewhere.text
    assert "used for another line" in elsewhere.json()["message"]
