"""Looking after contacts, end to end: forgetting, exporting, the cap, and loops.

Each test drives the real path from an inbound email (or an API call) to the
database, with only the model's next move scripted:

* forgetting a contact takes their rows, handles, conversations, web chat
  sessions and stored profile, and blanks what of theirs reached a member;
* exporting one hands over their rows too, a page at a time;
* past the contacts cap no run starts, the member is told, and the contact
  hears once that a person will reply;
* setting the cap is an owner's, and writing first to a contact an editor's,
  a few times a day at most;
* an auto-reply is never answered, and "STOP" unsubscribes;
* a contact's history stays with them when the member looking after contacts
  changes, and a member leaving is told to the pod's admins.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.contracts import contact_conversations
from app.modules.agent.domain.outsiders import HANDED_TO_KEY
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.infrastructure.models.conversation import (
    AgentRunModel,
    MessageModel,
)
from app.modules.agent_surfaces.composition import (
    build_surface_ingress,
    build_surface_turn_starter,
)
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.contracts.contacts import REDACTED_TITLE
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.domain.notification import (
    NotificationDeliveryStatus,
    NotificationEntity,
    NotificationOriginKind,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceExternalUser,
    NotificationModel,
)
from app.modules.agent_surfaces.infrastructure.repositories.notification_repository import (  # noqa: E501
    NotificationRepository,
)
from app.modules.contacts.infrastructure.visitor_sessions import (
    VisitorCodeModel,
    VisitorSessionModel,
)
from app.modules.agent_surfaces.events import handlers
from app.modules.agent_surfaces.services.contact_keepers import UNATTENDED_WARNING
from app.modules.agent_surfaces.tests.e2e.helpers import _resend_payload
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.test_email_contacts_e2e import (
    CUSTOMER,
    _email_bot,
)
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact
from app.modules.contacts.infrastructure.models import (
    ContactIdentityModel,
    ContactModel,
)
from app.modules.datastore.contracts import contact_rows
from app.modules.pod.contracts.members import pod_member_id
from app.modules.pod.domain.events import PodMemberRemovedEvent
from app.modules.test_support.fakes import PassthroughEventInbox
from app.modules.usage.contracts import UsageLimitExceededError
from app.modules.usage.contracts.contacts_cap import contacts_cap_reached
from app.modules.usage.contracts.metering import check_run_budget
from app.modules.usage.domain.accounting import CONTACT_RUN
from app.modules.usage.infrastructure.models import UsageRecord
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e


async def _dana_writes(
    db_session: AsyncSession,
    address: str,
    message_id: str,
    text: str = "Where is my order?",
    *,
    in_reply_to: str | None = None,
) -> SurfaceChatContext:
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id=message_id,
                text=text,
                in_reply_to=f"<{in_reply_to}@resend-e2e.test>" if in_reply_to else None,
                references=[f"<{in_reply_to}@resend-e2e.test>"]
                if in_reply_to
                else None,
            ),
            headers={},
        ),
        script=[script_text("On its way.")],
    )
    assert isinstance(context, SurfaceChatContext)
    return context


async def _prepare(db_session: AsyncSession, payload: dict):
    """Ingress alone: what the webhook decides, before any run is driven."""
    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(
        SurfacePlatformWebhookIngress(source="resend", payload=payload, headers={})
    )
    await uow.commit()
    return context


async def _contact_owned_orders(client: AsyncClient, pod_id: str) -> None:
    created = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": "orders",
            "primary_key_column": "id",
            "enable_rls": False,
            "contact_owned": True,
            "contact_columns": ["item"],
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "item", "type": "TEXT", "required": True},
            ],
        },
    )
    assert created.status_code == 201, created.text


async def _order(client: AsyncClient, pod_id: str, contact_id: str, item: str) -> None:
    created = await client.post(
        f"/pods/{pod_id}/datastore/tables/orders/records",
        json={"data": {"item": item, "contact_id": contact_id}},
    )
    assert created.status_code == 201, created.text


async def _dana(client: AsyncClient, pod_id: str) -> str:
    listed = await client.get(f"/pods/{pod_id}/contacts")
    assert listed.status_code == 200, listed.text
    [contact] = listed.json()["items"]
    return contact["id"]


async def _member(
    owner_client: AsyncClient,
    async_client: AsyncClient,
    *,
    org_id: str,
    pod_id: str,
    role: str,
    prefix: str,
) -> dict:
    user = await signup_user(async_client, prefix)
    org_member = await invite_org_member(
        owner_client, async_client, org_id=org_id, user=user
    )
    await add_pod_member(
        owner_client, pod_id=pod_id, organization_member_id=org_member["id"], role=role
    )
    return user


# ------------------------------------------------------------ forgetting


async def test_forgetting_a_contact_takes_everything_the_pod_held_about_them(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    owner = UUID(fixed_test_user["id"])
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    await _contact_owned_orders(authenticated_client, pod_id)
    context = await _dana_writes(db_session, address, "forget-1")
    dana = await _dana(authenticated_client, pod_id)
    await _order(authenticated_client, pod_id, dana, "Blue kettle")
    await _order(authenticated_client, pod_id, str(uuid4()), "Somebody else's")

    # A web chat session that named her, with a code, and a question of hers a
    # bot passed on to the member.
    widget = await authenticated_client.post(
        f"/pods/{pod_id}/web-widgets", json={"name": "Shop chat"}
    )
    assert widget.status_code == 201, widget.text
    now = datetime.now(timezone.utc)
    session_row = VisitorSessionModel(
        pod_id=UUID(pod_id),
        widget_id=UUID(widget.json()["id"]),
        secret_hash=uuid4().hex,
        contact_id=UUID(dana),
        strength="CODE",
        last_seen_at=now,
        expires_at=now + timedelta(days=30),
    )
    db_session.add(session_row)
    await db_session.flush()
    session_id = session_row.id
    db_session.add(
        VisitorCodeModel(
            session_id=session_id,
            email=CUSTOMER,
            code_hash=uuid4().hex,
            expires_at=now + timedelta(minutes=10),
            attempts=0,
        )
    )
    uow = SqlAlchemyUnitOfWork(db_session)
    member_id = await pod_member_id(uow, UUID(pod_id), owner)
    assert member_id is not None
    question = await NotificationRepository(uow).create(
        NotificationEntity(
            pod_id=UUID(pod_id),
            recipient_user_id=owner,
            recipient_pod_member_id=member_id,
            origin_kind=NotificationOriginKind.API,
            origin_conversation_id=context.conversation_id,
            from_outside=True,
            asked_by_name="Dana",
            title="A question from outside the space",
            body="Can I change my delivery address to 4 Elm Row?",
            expects_response=False,
            delivery_status=NotificationDeliveryStatus.UNDELIVERABLE,
        )
    )
    if (
        await db_session.scalar(
            select(AgentSurfaceExternalUser.id).where(
                AgentSurfaceExternalUser.platform == "RESEND",
                AgentSurfaceExternalUser.external_user_id == CUSTOMER,
            )
        )
    ) is None:
        db_session.add(
            AgentSurfaceExternalUser(
                platform="RESEND", external_user_id=CUSTOMER, raw_profile={}
            )
        )
    await db_session.commit()

    forgotten = await authenticated_client.delete(f"/pods/{pod_id}/contacts/{dana}")
    assert forgotten.status_code == 204, forgotten.text

    db_session.expire_all()
    records = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/orders/records"
    )
    assert [row["item"] for row in records.json()["items"]] == ["Somebody else's"]
    assert (
        await db_session.scalar(
            select(ContactIdentityModel.id).where(
                ContactIdentityModel.contact_id == UUID(dana)
            )
        )
    ) is None
    assert await db_session.get(ConversationModel, context.conversation_id) is None
    assert await db_session.get(VisitorSessionModel, session_id) is None
    assert (
        await db_session.scalar(
            select(VisitorCodeModel.id).where(VisitorCodeModel.session_id == session_id)
        )
    ) is None
    assert (
        await db_session.scalar(
            select(AgentSurfaceExternalUser.id).where(
                AgentSurfaceExternalUser.platform == "RESEND",
                AgentSurfaceExternalUser.external_user_id == CUSTOMER,
            )
        )
    ) is None
    note = await db_session.get(NotificationModel, question.id)
    assert note is not None
    assert (note.title, note.body, note.asked_by_name) == (REDACTED_TITLE, "", None)

    again = await authenticated_client.delete(f"/pods/{pod_id}/contacts/{dana}")
    assert again.status_code == 404


# ------------------------------------------------------------ listing


async def test_contacts_made_at_one_moment_page_without_one_lost(
    authenticated_client: AsyncClient, db_session: AsyncSession, test_pod
):
    pod_id = UUID(test_pod["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    for index in range(3):
        await open_contact(
            uow,
            pod_id=pod_id,
            kind=IdentityKind.EMAIL,
            value=f"customer{index}@client.example",
            strength=IdentityStrength.CHANNEL,
            display_name=None,
        )
    # One timestamp for all three: a cursor of the time alone would skip two.
    await db_session.execute(
        update(ContactModel)
        .where(ContactModel.pod_id == pod_id)
        .values(created_at=datetime(2026, 10, 1, 9, tzinfo=timezone.utc))
    )
    await db_session.commit()

    seen: list[str] = []
    before: str | None = None
    for _ in range(5):
        params: dict[str, str | int] = {"limit": 1}
        if before:
            params["before"] = before
        page = await authenticated_client.get(f"/pods/{pod_id}/contacts", params=params)
        assert page.status_code == 200, page.text
        seen += [item["id"] for item in page.json()["items"]]
        before = page.json().get("next_before")
        if not before:
            break

    assert len(seen) == 3 and len(set(seen)) == 3
    refused = await authenticated_client.get(
        f"/pods/{pod_id}/contacts", params={"before": "yesterday"}
    )
    assert refused.status_code == 400


# ------------------------------------------------------------ exporting


async def test_an_export_carries_their_rows_and_pages_through_everything(
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
    await _contact_owned_orders(authenticated_client, pod_id)
    # Two separate emails: two conversations.
    await _dana_writes(db_session, address, "export-1", "First question")
    await _dana_writes(db_session, address, "export-2", "Second question")
    dana = await _dana(authenticated_client, pod_id)
    for item in ("Blue kettle", "Teapot", "Saucer"):
        await _order(authenticated_client, pod_id, dana, item)
    await _order(authenticated_client, pod_id, str(uuid4()), "Somebody else's")
    # One of each to a page, so the export has to be followed to the end.
    monkeypatch.setattr(contact_conversations, "MAX_EXPORTED_CONVERSATIONS", 1)
    monkeypatch.setattr(contact_rows, "MAX_CONTACT_ROWS", 2)

    conversations, rows, pages = [], [], 0
    cursor: str | None = None
    while True:
        params = {"cursor": cursor} if cursor else {}
        page = await authenticated_client.get(
            f"/pods/{pod_id}/contacts/{dana}/export", params=params
        )
        assert page.status_code == 200, page.text
        body = page.json()
        assert body["contact"]["id"] == dana
        conversations += body["conversations"]
        rows += body["rows"]
        pages += 1
        cursor = body.get("next_cursor")
        if not cursor:
            break
        assert pages < 10, "the export never ended"

    assert pages >= 3
    said = {m["text"] for c in conversations for m in c["messages"]}
    assert {"First question", "Second question"} <= said
    assert len(conversations) == 2
    assert sorted(row["values"]["item"] for row in rows) == [
        "Blue kettle",
        "Saucer",
        "Teapot",
    ]
    assert {row["table"] for row in rows} == {"orders"}

    refused = await authenticated_client.get(
        f"/pods/{pod_id}/contacts/{dana}/export", params={"cursor": "junk"}
    )
    assert refused.status_code == 400


# ------------------------------------------------------------ the contacts cap


async def test_past_the_cap_no_run_starts_and_a_person_takes_over(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    owner = UUID(fixed_test_user["id"])
    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    first = await _dana_writes(db_session, address, "cap-1")
    capped = await authenticated_client.put(
        f"/usage/organizations/{fixed_test_org['id']}/contacts-cap",
        json={"monthly_limit_usd": 0},
    )
    assert capped.status_code == 200, capped.text
    sent_before = len(message_store.get_all("RESEND"))
    starter = build_surface_turn_starter(SessionUnitOfWorkFactory(async_session_maker))

    async def write(message_id: str, text: str) -> None:
        context = await _prepare(
            db_session,
            _resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id=message_id,
                text=text,
                in_reply_to="<cap-1@resend-e2e.test>",
                references=["<cap-1@resend-e2e.test>"],
            ),
        )
        assert isinstance(context, SurfaceChatContext)
        assert context.conversation_id == first.conversation_id
        await starter.execute_chat(context)

    runs_before = await _runs(db_session, first.conversation_id)
    await write("cap-2", "Hello again?")
    await write("cap-3", "Anyone there?")

    db_session.expire_all()
    # No run for either message, and both are in the conversation.
    assert await _runs(db_session, first.conversation_id) == runs_before
    texts = [
        (row.role, row.text)
        for row in (
            await db_session.scalars(
                select(MessageModel)
                .where(MessageModel.conversation_id == first.conversation_id)
                .order_by(MessageModel.sequence)
            )
        ).all()
    ]
    assert ("user", "Hello again?") in texts and ("user", "Anyone there?") in texts
    # Told once that a person will reply -- in the thread and by email.
    replies = [text for role, text in texts if text and "will reply here" in text]
    assert len(replies) == 1 and replies[0].startswith("A person from ")
    sent = message_store.get_all("RESEND")[sent_before:]
    assert len(sent) == 1 and "will reply here" in str(sent[0])
    # The member was told, and has the conversation.
    notes = (
        await db_session.scalars(
            select(NotificationModel).where(
                NotificationModel.recipient_user_id == owner,
                NotificationModel.origin_conversation_id == first.conversation_id,
            )
        )
    ).all()
    assert len(notes) == 1
    assert "Hello again?" in notes[0].body and "Anyone there?" not in notes[0].body
    conversation = await db_session.get(ConversationModel, first.conversation_id)
    assert conversation is not None
    assert (conversation.conversation_metadata or {})[HANDED_TO_KEY]["user_id"] == str(
        owner
    )


async def _runs(db_session: AsyncSession, conversation_id: UUID) -> int:
    return len(
        (
            await db_session.scalars(
                select(AgentRunModel.id).where(
                    AgentRunModel.conversation_id == conversation_id
                )
            )
        ).all()
    )


async def test_the_default_cap_holds_until_an_owner_removes_it(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    fixed_test_org,
    fixed_test_user,
):
    org_id = UUID(fixed_test_org["id"])
    db_session.add(
        UsageRecord(
            user_id=UUID(fixed_test_user["id"]),
            organization_id=org_id,
            occurred_at=datetime.now(timezone.utc),
            source_type=CONTACT_RUN,
            profile_id="contacts",
            profile_scope="SYSTEM",
            model_name="m",
            cost_amount=Decimal("60"),
        )
    )
    await db_session.commit()

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        assert await contacts_cap_reached(uow, organization_id=org_id)
        # The pre-run check every outside run makes refuses it too.
        with pytest.raises(UsageLimitExceededError):
            await check_run_budget(
                factory=SessionUnitOfWorkFactory(async_session_maker),
                organization_id=org_id,
                user_id=UUID(fixed_test_user["id"]),
                profile_scope="SYSTEM",
                outside_audience=True,
            )

    removed = await authenticated_client.put(
        f"/usage/organizations/{org_id}/contacts-cap",
        json={"monthly_limit_usd": None},
    )
    assert removed.status_code == 200, removed.text
    assert removed.json()["spent_this_month_usd"] == 60

    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        assert not await contacts_cap_reached(uow, organization_id=org_id)


async def test_only_an_owner_sets_the_contacts_cap(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    org_id = fixed_test_org["id"]
    editor = await signup_user(async_client, "cap-editor")
    invite = await authenticated_client.post(
        f"/organizations/{org_id}/invitations",
        json={"email": editor["email"], "role": "ORG_EDITOR"},
    )
    assert invite.status_code == 201, invite.text
    accepted = await async_client.post(
        f"/organizations/invitations/{invite.json()['id']}/accept",
        headers=auth_headers(editor),
    )
    assert accepted.status_code == 200, accepted.text

    read = await async_client.get(
        f"/usage/organizations/{org_id}/contacts-cap", headers=auth_headers(editor)
    )
    assert read.status_code == 200, read.text
    refused = await async_client.put(
        f"/usage/organizations/{org_id}/contacts-cap",
        json={"monthly_limit_usd": None},
        headers=auth_headers(editor),
    )
    assert refused.status_code == 403, refused.text
    unchanged = await authenticated_client.get(
        f"/usage/organizations/{org_id}/contacts-cap"
    )
    assert unchanged.json()["is_default"] is True


# ------------------------------------------------------------ follow-ups


async def test_writing_first_to_a_contact_takes_an_editor_and_is_rate_limited(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
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
    await _dana_writes(db_session, address, "follow-limit-1")
    dana = await _dana(authenticated_client, pod_id)
    user = await _member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_USER",
        prefix="follow-user",
    )

    # A pod user can read the contact, but not speak for the pod to them.
    seen = await async_client.get(
        f"/pods/{pod_id}/contacts/{dana}", headers=auth_headers(user)
    )
    assert seen.status_code == 200, seen.text
    refused = await async_client.post(
        f"/pods/{pod_id}/contacts/{dana}/messages",
        json={"message": "Hello from me"},
        headers=auth_headers(user),
    )
    assert refused.status_code == 403, refused.text

    monkeypatch.setattr(
        surface_settings, "surface_contact_follow_ups_per_contact_per_day", 1
    )
    sent = await authenticated_client.post(
        f"/pods/{pod_id}/contacts/{dana}/messages",
        json={"message": "Your order shipped today."},
    )
    assert sent.status_code == 200, sent.text
    limited = await authenticated_client.post(
        f"/pods/{pod_id}/contacts/{dana}/messages",
        json={"message": "And another thing."},
    )
    assert limited.status_code == 429, limited.text


async def test_a_follow_up_the_platform_refused_is_never_shown_as_said(
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
    context = await _dana_writes(db_session, address, "not-sent-1")
    uow = SqlAlchemyUnitOfWork(db_session)
    await contact_conversations.append_follow_up(
        uow,
        conversation_id=context.conversation_id,
        message="This one never left.",
        sent_by_user_id=UUID(fixed_test_user["id"]),
        delivered=False,
    )
    await contact_conversations.append_follow_up(
        uow,
        conversation_id=context.conversation_id,
        message="This one did.",
        sent_by_user_id=UUID(fixed_test_user["id"]),
        delivered=True,
    )
    await db_session.commit()

    shown = await contact_conversations.visible_messages(
        uow, context.conversation_id, after=-1, limit=100
    )

    texts = [message.text for message in shown]
    assert "This one did." in texts
    assert "This one never left." not in texts


# ------------------------------------------------------------ loops and STOP


async def test_an_auto_reply_is_neither_answered_nor_made_a_contact(
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
    before = len(message_store.get_all("RESEND"))
    payload = _resend_payload(
        sender_email=CUSTOMER,
        assistant_address=address,
        message_id="ooo-1",
        text="I am out of the office until Monday.",
        subject="Automatic reply: Prices",
    )
    payload["headers"]["auto-submitted"] = "auto-replied"

    assert await _prepare(db_session, payload) is None
    assert len(message_store.get_all("RESEND")) == before
    listed = await authenticated_client.get(f"/pods/{pod_id}/contacts")
    assert listed.json()["items"] == []


async def test_stop_unsubscribes_the_address_it_came_from(
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
    await _dana_writes(db_session, address, "stop-1")
    dana = await _dana(authenticated_client, pod_id)

    stopped = await _prepare(
        db_session,
        _resend_payload(
            sender_email=CUSTOMER,
            assistant_address=address,
            message_id="stop-2",
            text="STOP",
        ),
    )

    assert stopped is None
    db_session.expire_all()
    identity = await db_session.scalar(
        select(ContactIdentityModel).where(
            ContactIdentityModel.contact_id == UUID(dana)
        )
    )
    assert identity is not None and identity.unsubscribed_at is not None
    refused = await authenticated_client.post(
        f"/pods/{pod_id}/contacts/{dana}/messages",
        json={"message": "One more thing."},
    )
    assert refused.status_code == 409, refused.text


# ------------------------------------------------------------ who looks after them


async def test_a_contacts_history_moves_with_them_to_a_new_keeper(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="anyone",
    )
    first = await _dana_writes(db_session, address, "keeper-1", "Order 1182?")
    keeper = await _member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_EDITOR",
        prefix="keeper",
    )
    moved = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/{surface['name']}",
        json={
            "config": {
                "contacts": {"answer": "anyone", "looked_after_by": keeper["id"]}
            }
        },
    )
    assert moved.status_code == 200, moved.text

    again = await _dana_writes(
        db_session, address, "keeper-2", "Any news?", in_reply_to="keeper-1"
    )

    assert again.conversation_id == first.conversation_id
    assert again.user_id == UUID(keeper["id"])
    db_session.expire_all()
    conversation = await db_session.get(ConversationModel, first.conversation_id)
    assert conversation is not None and conversation.user_id == UUID(keeper["id"])

    # The keeper leaves: the admins hear of it, and the bot's settings say so.
    members = await authenticated_client.get(f"/pods/{pod_id}/members")
    assert members.status_code == 200, members.text
    keeper_member = next(
        item
        for item in members.json()["items"]
        if str(item.get("user_id")) == keeper["id"]
    )
    removed = await authenticated_client.delete(
        f"/pods/{pod_id}/members/{keeper_member['pod_member_id']}"
    )
    assert removed.status_code == 204, removed.text
    # As the pod stream delivers it to the surfaces' consumer.
    await handlers.on_pod_deleted(
        {
            "event_type": PodMemberRemovedEvent.get_event_type(),
            "pod_id": pod_id,
            "user_id": keeper["id"],
        },
        logging.getLogger("test"),
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        inbox=PassthroughEventInbox(),
    )
    shown = await authenticated_client.get(f"/pods/{pod_id}/surfaces/{surface['name']}")
    assert shown.json()["contacts_warning"] == UNATTENDED_WARNING
    notes = (
        await db_session.scalars(
            select(NotificationModel.title).where(
                NotificationModel.pod_id == UUID(pod_id),
                NotificationModel.recipient_user_id == UUID(fixed_test_user["id"]),
            )
        )
    ).all()
    assert "Nobody is looking after contacts" in notes
