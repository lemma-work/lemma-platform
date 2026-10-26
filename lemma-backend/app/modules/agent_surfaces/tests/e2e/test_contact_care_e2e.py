"""Looking after contacts, end to end: forgetting, exporting, the cap, and loops.

Each test drives the real path from an inbound email (or an API call) to the
database, with only the model's next move scripted:

* forgetting a contact takes their rows, handles, conversations, web chat
  sessions and stored profile, and blanks what of theirs reached a member;
* exporting one hands over their rows too, a page at a time;
* past the contacts cap no run starts, the contact's channel waits on a
  person, the member is told where to look, and the contact hears once that a
  person will reply;
* a contact's run that needs a person calls one in with ``ask_a_person``: the
  channel waits on a person, and the member who looks after them is told;
* setting the cap is an owner's, and writing to a contact -- a line in their
  channel, carried to them afterwards -- an editor's, a few times a day at most;
* an auto-reply is never answered, and "STOP" unsubscribes -- a member's line
  to them after it stays in Lemma, recorded as not sent and why;
* a contact's history stays with them when the member looking after contacts
  changes, and a member leaving is told to the pod's admins.
"""

from __future__ import annotations

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
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.agent.contracts import contact_conversations
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.infrastructure.models.conversation import (
    AgentRunModel,
    MessageModel,
)
from app.modules.agent_surfaces.composition import (
    build_surface_ingress,
    build_surface_turn_starter,
)
from app.modules.contacts.config import contacts_settings
from app.modules.agent.infrastructure.tools.messaging.people import call_a_person_in
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurfaceExternalUser
from app.modules.contacts.infrastructure.visitor_sessions import (
    VisitorCodeModel,
    VisitorSessionModel,
)
from app.modules.agent_surfaces.services.contact_keepers import UNATTENDED_WARNING
from app.modules.agent_surfaces.tests.e2e.helpers import _resend_payload
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    record_model_requests,
    script_text,
    script_tool_call,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    contact_channel_id,
    send_deliveries,
    deliveries_of_line,
    direct_lines,
    member_writes,
)
from app.modules.agent_surfaces.tests.e2e.test_email_contacts_e2e import (
    CUSTOMER,
    _email_bot,
)
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact
from app.modules.contacts.domain.handles import handle_hash
from app.modules.contacts.infrastructure.models import (
    ContactIdentityModel,
    ContactSuppressionModel,
    ContactModel,
)
from app.modules.datastore.contracts import contact_rows
from app.modules.channels.contracts import NewLine, Sender, contact_channel, post
from app.modules.channels.infrastructure.models import ChannelCaseModel, ChannelModel
from app.modules.channels.tests.e2e.push_support import pushed
from app.modules.usage.contracts import UsageLimitExceededError
from app.modules.usage.contracts.contacts_cap import contacts_cap_reached
from app.modules.usage.contracts.metering import check_run_budget
from app.modules.usage.domain.accounting import CONTACT_RUN
from app.modules.usage.infrastructure.models import UsageRecord
from app.modules.test_support.query_counting import (
    counted_queries,
    format_statements,
    statements_touching,
)
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

    # A web chat session that named her, with a code -- and the bot called a
    # person in for her, which flags her channel and leaves a note there.
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
    assert await call_a_person_in(
        pod_id=UUID(pod_id),
        conversation_id=context.conversation_id,
        keeper_user_id=owner,
        reason="She wants to change her delivery address.",
        key=None,
    )
    her_channel = await db_session.scalar(
        select(ChannelModel.id).where(ChannelModel.contact_id == UUID(dana))
    )
    assert her_channel is not None

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
    # Her channel goes with her: her lines, and the note calling a person in.
    assert await db_session.get(ChannelModel, her_channel) is None

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
    cursor: str | None = None
    for _ in range(5):
        params: dict[str, str | int] = {"limit": 1}
        if cursor:
            params["cursor"] = cursor
        page = await authenticated_client.get(f"/pods/{pod_id}/contacts", params=params)
        assert page.status_code == 200, page.text
        seen += [item["id"] for item in page.json()["items"]]
        cursor = page.json().get("next_cursor")
        if not cursor:
            break

    assert len(seen) == 3 and len(set(seen)) == 3
    for not_ours in ("yesterday", "bm90IGEgY3Vyc29y"):
        refused = await authenticated_client.get(
            f"/pods/{pod_id}/contacts", params={"cursor": not_ours}
        )
        assert refused.status_code == 400, not_ours


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
    # Two separate emails: one conversation, Dana's with the pod's agent.
    await _dana_writes(db_session, address, "export-1", "First question")
    await _dana_writes(db_session, address, "export-2", "Second question")
    dana = await _dana(authenticated_client, pod_id)
    for item in ("Blue kettle", "Teapot", "Saucer"):
        await _order(authenticated_client, pod_id, dana, item)
    await _order(authenticated_client, pod_id, str(uuid4()), "Somebody else's")
    # One of each to a page, so the export has to be followed to the end.
    monkeypatch.setattr(contact_conversations, "MAX_EXPORTED_CONVERSATIONS", 1)
    monkeypatch.setattr(contact_rows, "MAX_CONTACT_ROWS", 1)

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
    assert len(conversations) == 1
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


@pytest.mark.drives_work
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
    # Told once that a person will reply -- in the thread and by email, as the
    # pod's line at her place carried by its delivery: her rules apply, and
    # the email says how to stop, in its body and its headers.
    replies = [text for role, text in texts if text and "will reply here" in text]
    assert len(replies) == 1 and replies[0].startswith("A person from ")
    assert message_store.get_all("RESEND")[sent_before:] == []
    await send_deliveries()
    sent = message_store.get_all("RESEND")[sent_before:]
    [courtesy] = [mail for mail in sent if "will reply here" in str(mail)]
    assert courtesy["to"] == [CUSTOMER]
    assert "List-Unsubscribe" in courtesy["headers"]
    # The member was told once, in their own channel, where to look -- not
    # what she wrote: her words stay in her channel, which forgetting her takes.
    # Pushed: nobody else is answering her.
    [note] = await direct_lines(db_session, pod_id=pod_id, user_id=owner)
    assert note.members_only
    assert "Somebody outside the space is waiting for you" in note.body
    assert "Hello again?" not in note.body and "Anyone there?" not in note.body
    assert [push.recipient_user_id for push in await deliveries_of_line(note.id)] == [
        owner
    ]
    # Both are on Dana's channel, which waits on a person to answer by hand.
    conversation = await db_session.get(ConversationModel, first.conversation_id)
    assert conversation is not None and conversation.channel_id is not None
    channel = f"/pods/{pod_id}/channels/{conversation.channel_id}"
    shown = await authenticated_client.get(channel)
    assert shown.status_code == 200, shown.text
    assert (
        shown.json()["attention"]["reason"] == "The monthly cap on answering contacts"
    )
    timeline = await authenticated_client.get(f"{channel}/messages")
    heard = [
        line["text"]
        for line in timeline.json()["items"]
        if line["sender"]["kind"] == "CONTACT"
    ]
    assert {"Hello again?", "Anyone there?"} <= set(heard)


async def _contact_writes_on(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    *,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """Dana, a contact on the email bot, and a way for her to write again."""
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
    first = await _dana_writes(db_session, address, "step-1")
    conversation = await db_session.get(ConversationModel, first.conversation_id)
    assert conversation is not None and conversation.channel_id is not None
    starter = build_surface_turn_starter(SessionUnitOfWorkFactory(async_session_maker))

    async def write(message_id: str, text: str) -> None:
        context = await _prepare(
            db_session,
            _resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id=message_id,
                text=text,
                in_reply_to="<step-1@resend-e2e.test>",
                references=["<step-1@resend-e2e.test>"],
            ),
        )
        assert isinstance(context, SurfaceChatContext)
        await starter.execute_chat(context)

    async def runs() -> int:
        db_session.expire_all()
        return await _runs(db_session, first.conversation_id)

    return conversation.channel_id, write, runs


async def test_a_contacts_agent_switched_off_leaves_them_to_members(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """Off, nothing answers the contact but a member, and nothing tells them a
    person will: the channel waits on a reply. On again, the agent answers."""
    channel_id, write, runs = await _contact_writes_on(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    channel = f"/pods/{test_pod['id']}/channels/{channel_id}"
    off = await authenticated_client.patch(channel, json={"agents_answer": False})
    assert off.status_code == 200, off.text
    assert off.json()["agents_answer"] is False
    before = await runs()

    await write("step-2", "Can a person look at this?")

    assert await runs() == before
    waiting = (await authenticated_client.get(channel)).json()
    assert waiting["attention"]["reason"] == "Waiting on a reply: the agent is off"
    replies = await db_session.scalars(
        select(MessageModel.text).where(
            MessageModel.conversation_id.in_(
                select(ConversationModel.id).where(
                    ConversationModel.channel_id == channel_id
                )
            )
        )
    )
    assert not any("will reply" in str(text) for text in replies)

    on = await authenticated_client.patch(channel, json={"agents_answer": True})
    assert on.status_code == 200, on.text
    await write("step-3", "Thanks, back to the bot.")

    assert await runs() == before + 1


async def test_a_member_who_writes_holds_the_contact_until_they_go_quiet(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """Writing to a contact steps in: the agent waits while the member is in
    the conversation, and answers again once they have been quiet past the
    channel's window -- telling the member it has."""
    channel_id, write, runs = await _contact_writes_on(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    channel = f"/pods/{test_pod['id']}/channels/{channel_id}"
    wrote = await authenticated_client.post(
        f"{channel}/messages", json={"text": "Hi Dana, I can help with that."}
    )
    assert wrote.status_code == 201, wrote.text
    stepped_in = (await authenticated_client.get(channel)).json()
    assert stepped_in["assignee"]["id"] == fixed_test_user["id"]
    before = await runs()

    await write("step-2", "Great, what are the options?")
    assert await runs() == before

    window = await authenticated_client.patch(
        channel, json={"hand_back_after_hours": 1}
    )
    assert window.json()["assignee"]["id"] == fixed_test_user["id"]
    # The member last wrote two hours ago: past the window.
    await db_session.execute(
        update(ChannelCaseModel)
        .where(ChannelCaseModel.channel_id == channel_id)
        .values(assignee_last_line_at=datetime.now(timezone.utc) - timedelta(hours=2))
    )
    await db_session.commit()

    await write("step-3", "Hello? Anyone there?")

    assert await runs() == before + 1
    assert (await authenticated_client.get(channel)).json()["assignee"] is None
    told = await direct_lines(
        db_session, pod_id=test_pod["id"], user_id=fixed_test_user["id"]
    )
    assert any("the agent answers them again" in line.body for line in told)


@pytest.mark.drives_work
async def test_a_member_assigned_a_contact_is_told_when_they_write(
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
    """Handed a contact, an editor hears from them as soon as they write --
    pushed, unread in their channel, and in their inbox as waiting on a
    reply -- while the agent stays quiet; the contact is told once that a
    person will reply. The editor writing back ends the wait."""
    pod_id = test_pod["id"]
    channel_id, write, runs = await _contact_writes_on(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    acknowledging = await authenticated_client.put(
        f"/pods/{pod_id}",
        json={"config": {"contacts": {"auto_acknowledge": "A person will reply."}}},
    )
    assert acknowledging.status_code == 200, acknowledging.text
    editor = await _member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_EDITOR",
        prefix="assignee",
    )
    as_editor = auth_headers(editor)
    channel = f"/pods/{pod_id}/channels/{channel_id}"
    assigned = await authenticated_client.patch(
        channel, json={"assignee": editor["id"]}
    )
    assert assigned.status_code == 200, assigned.text
    before = await runs()

    await write("step-2", "Is anybody looking at this?")

    assert await runs() == before
    seen = (await async_client.get(channel, headers=as_editor)).json()
    assert seen["joined"] is True
    assert seen["unread"]["lines"] >= 1
    assert seen["attention"]["reason"] == (
        "Waiting on a reply from the member answering them"
    )
    inbox = (await async_client.get(f"/pods/{pod_id}/inbox", headers=as_editor)).json()
    assert inbox["totals"]["contacts"] == 1
    assert [each["id"] for each in inbox["contacts"]] == [str(channel_id)]
    timeline = (await async_client.get(f"{channel}/messages", headers=as_editor)).json()
    [theirs] = [
        line
        for line in timeline["items"]
        if line["text"] == "Is anybody looking at this?"
    ]
    assert editor["id"] in await pushed(theirs["id"])
    # The push reads on its own: who wrote -- by what the pod calls her, never
    # "Somebody" -- and where to answer her, since a reply to the push itself
    # would go to the pod, not to her.
    await send_deliveries()
    [told] = [
        mail
        for mail in message_store.get_all("RESEND")
        if mail["to"] == [editor["email"]] and "Is anybody looking" in mail["text"]
    ]
    assert f"{theirs['sender']['name'] or CUSTOMER} (contact)" in told["text"]
    assert "Somebody" not in told["text"]
    assert f"/t/{pod_id}/channel/{channel_id}" in told["text"]
    said = [
        line for line in timeline["items"] if line["text"] == "A person will reply."
    ]
    assert len(said) == 1
    # Said after what it answers, as the contact's own chat shows it.
    assert said[0]["seq"] > theirs["seq"]

    replied = await async_client.post(
        f"{channel}/messages", json={"text": "Hi Dana, looking now."}, headers=as_editor
    )
    assert replied.status_code == 201, replied.text
    await write("step-3", "Thanks!")

    shown = (await async_client.get(channel, headers=as_editor)).json()
    # Waiting on a reply again, but the editor is in the conversation: the
    # contact is not told a person will reply on every line.
    assert shown["attention"] is not None
    timeline = (await async_client.get(f"{channel}/messages", headers=as_editor)).json()
    assert [
        line["text"]
        for line in timeline["items"]
        if line["text"] == "A person will reply."
    ] == ["A person will reply."]
    await async_client.post(
        f"{channel}/messages", json={"text": "You're welcome."}, headers=as_editor
    )
    assert (await async_client.get(channel, headers=as_editor)).json()[
        "attention"
    ] is None
    inbox = (await async_client.get(f"/pods/{pod_id}/inbox", headers=as_editor)).json()
    assert inbox["totals"]["contacts"] == 0


async def test_a_contacts_run_calls_a_person_in_and_the_keeper_is_told_where(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """What a contact needs from a person is asked with ``ask_a_person``.

    It used to be a notification to the member, with the contact's question
    quoted and an answer carried back through an approval card. Now the
    contact's channel waits on a person -- in Contacts, for every member who
    sees them, with the reason on it -- and the member who looks after them is
    told where to look. Stepping in answers the call. A contact's
    run has nothing else that reaches a member: no ``channel_post``.
    """
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
    seen = record_model_requests(monkeypatch)
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id="person-1",
                text="Can I get a refund on order 1182?",
            ),
            headers={},
        ),
        script=[
            script_tool_call(
                "ask_a_person",
                {"reason": "A refund on order 1182 is a member's call."},
                tool_call_id="call-1",
            ),
            script_text("Someone from the team will reply here."),
        ],
    )
    assert isinstance(context, SurfaceChatContext)

    assert seen, "the contact's run asked the model nothing"
    assert all("ask_a_person" in request["tools"] for request in seen)
    assert all("channel_post" not in request["tools"] for request in seen)
    conversation = await db_session.get(ConversationModel, context.conversation_id)
    assert conversation is not None and conversation.channel_id is not None
    channel = f"/pods/{pod_id}/channels/{conversation.channel_id}"
    shown = (await authenticated_client.get(channel)).json()
    assert shown["kind"] == "CONTACT"
    assert shown["attention"]["reason"] == "A refund on order 1182 is a member's call."
    [notice] = await direct_lines(db_session, pod_id=pod_id, user_id=owner)
    assert "A contact you look after needs a person" in notice.body

    # Writing to them is stepping in, and answers the call.
    taken = await authenticated_client.post(
        f"{channel}/messages", json={"text": "Hi Dana, let me look at that refund."}
    )
    assert taken.status_code == 201, taken.text
    assert (await authenticated_client.get(channel)).json()["attention"] is None


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

    # Asked in a unit of its own, as a surface asks it: a first ask counts the
    # month, and holds the counter it made until its unit ends.
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


async def test_past_the_cap_her_message_is_held_without_a_ledger_sum(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
    fixed_test_user,
    fake_resend,
    monkeypatch,
):
    """Each message she writes asks the cap before a turn starts, and reads it
    from the running total her run would be admitted on: once the month is
    counted, being held costs no sum over the month's spend."""
    _channel_id, write, runs = await _contact_writes_on(
        authenticated_client,
        db_session,
        test_pod=test_pod,
        fixed_test_user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
    )
    capped = await authenticated_client.put(
        f"/usage/organizations/{fixed_test_org['id']}/contacts-cap",
        json={"monthly_limit_usd": 0},
    )
    assert capped.status_code == 200, capped.text
    before = await runs()

    # The first ask under the new cap counts the month again.
    await write("step-2", "Hello again?")
    with counted_queries() as held:
        await write("step-3", "Anyone there?")

    assert await runs() == before
    sums = [
        statement
        for statement in held
        if "sum(" in statement.lower()
        and ("usage_records" in statement or "usage_limit_counters" in statement)
    ]
    assert not sums, format_statements(sums)
    assert statements_touching(held, "usage_limit_counters"), format_statements(held)


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


@pytest.mark.drives_work
async def test_writing_to_a_contact_takes_an_editor_and_a_daily_limit(
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
    """A member writes to a contact in the contact's channel; the line goes out
    afterwards, by its delivery (``surface.channel_delivery``). Past the day's limit the line is
    still the member's, in Lemma -- recorded as not sent, and why -- rather
    than the post being refused."""
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
    channel = await contact_channel_id(authenticated_client, pod_id, dana)
    user = await _member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_USER",
        prefix="follow-user",
    )

    # A pod user neither sees the contact nor speaks for the pod to them.
    as_user = auth_headers(user)
    unseen = await async_client.get(f"/pods/{pod_id}/contacts/{dana}", headers=as_user)
    assert unseen.status_code == 403, unseen.text
    theirs = await async_client.get(
        f"/pods/{pod_id}/channels", params={"kind": "CONTACT"}, headers=as_user
    )
    assert theirs.json()["items"] == []
    refused = await async_client.post(
        f"/pods/{pod_id}/channels/{channel}/messages",
        json={"text": "Hello from me"},
        headers=as_user,
    )
    assert refused.status_code == 404, refused.text

    monkeypatch.setattr(
        contacts_settings, "contacts_member_lines_per_contact_per_day", 1
    )
    # The agent's answer to her rides its own delivery: let it go first.
    await send_deliveries()
    sent_before = len(message_store.get_all("RESEND"))
    written = await member_writes(
        authenticated_client, pod_id, channel, "Your order shipped today."
    )
    assert written["sender"]["kind"] == "MEMBER"
    assert written["sender"]["id"] == fixed_test_user["id"]
    await send_deliveries()
    [delivery] = await deliveries_of_line(written["id"])
    assert (delivery.platform, delivery.status.value) == ("RESEND", "SENT")
    mails = message_store.get_all("RESEND")[sent_before:]
    assert len(mails) == 1 and "Your order shipped today." in str(mails[0])

    limited = await member_writes(
        authenticated_client, pod_id, channel, "And another thing."
    )
    await send_deliveries()
    [held] = await deliveries_of_line(limited["id"])
    assert held.status.value == "SKIPPED"
    assert held.reason == "That is as many messages as one contact gets in a day"
    assert len(message_store.get_all("RESEND")) == sent_before + 1


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
    assert context.audience.contact_id is not None
    member = UUID(fixed_test_user["id"])
    uow = SqlAlchemyUnitOfWork(db_session)
    channel = await contact_channel(
        uow, pod_id=UUID(pod_id), contact_id=context.audience.contact_id
    )
    for said, delivered in (("This one never left.", False), ("This one did.", True)):
        line = await post(
            uow,
            channel_id=channel.id,
            line=NewLine(sender=Sender.member(member), text=said),
        )
        assert line is not None
        await contact_conversations.append_follow_up(
            uow,
            conversation_id=context.conversation_id,
            line_id=line.id,
            sent_by_user_id=member,
            delivered=delivered,
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


@pytest.mark.drives_work
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
    assert identity is not None
    assert (
        await db_session.scalar(
            select(ContactSuppressionModel.reason).where(
                ContactSuppressionModel.pod_id == UUID(pod_id),
                ContactSuppressionModel.value_hash
                == handle_hash(IdentityKind.EMAIL, identity.value),
            )
        )
        == "stop"
    )
    # Members see it: a note in her channel, and on her handle and channel.
    channel = await contact_channel_id(authenticated_client, pod_id, dana)
    timeline = await authenticated_client.get(
        f"/pods/{pod_id}/channels/{channel}/messages"
    )
    [note] = [
        line
        for line in timeline.json()["items"]
        if line["text"] and "asked to stop" in line["text"]
    ]
    assert note["members_only"] is True
    assert "by email" in note["text"]
    shown = (await authenticated_client.get(f"/pods/{pod_id}/contacts/{dana}")).json()
    assert [handle["opted_out"] for handle in shown["identities"]] == [True]
    detail = await authenticated_client.get(f"/pods/{pod_id}/channels/{channel}")
    assert detail.json()["opted_out"] is True
    # A member's line to her afterwards stays in Lemma, recorded as not sent.
    sent_before = len(message_store.get_all("RESEND"))
    written = await member_writes(
        authenticated_client, pod_id, channel, "One more thing."
    )
    await send_deliveries()
    [held] = await deliveries_of_line(written["id"])
    assert held.status.value == "SKIPPED"
    assert held.reason == "The contact asked not to be written to there"
    assert len(message_store.get_all("RESEND")) == sent_before


# ------------------------------------------------------------ who looks after them


@pytest.mark.drives_work
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
    # Contacts' chats are an editor's or an admin's to see, so a member who
    # only uses the pod cannot be the one they belong to.
    user = await _member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        role="POD_USER",
        prefix="user",
    )
    refused = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/{surface['name']}",
        json={
            "config": {"contacts": {"answer": "anyone", "looked_after_by": user["id"]}}
        },
    )
    assert refused.status_code in (400, 422), refused.text
    assert "editor or admin" in refused.text
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
    # The removal wrote the surfaces' `surfaces.member_removed` item.
    await run_until_idle(async_session_maker, classes=[WorkClass.EVENTS])
    shown = await authenticated_client.get(f"/pods/{pod_id}/surfaces/{surface['name']}")
    assert shown.json()["contacts_warning"] == UNATTENDED_WARNING
    told = await direct_lines(db_session, pod_id=pod_id, user_id=fixed_test_user["id"])
    [note] = [line for line in told if "has left the space" in line.body]
    # A note to them alone, in their own channel: nothing waits on an answer.
    assert note.members_only
