"""Somebody outside the pod emails its bot, and is answered as a contact -- end to end.

The whole path with only the model's next move scripted: a pod's email bot set
to answer anyone, a message from an address no Lemma user has, authenticated by
the receiving mail service. The sender becomes a contact, the run belongs to
the member who turned contacts on, it is marked as the contact's, and asked to
list the pod's tables it sees only the one the pod marked Public.

And the two refusals that must stay refusals: mail that was not authenticated
is never answered (a reply would go to whoever ``From:`` names) but is parked
for the member, and a bot with contacts off answers no stranger.
"""

from __future__ import annotations

import json
import re
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.outsiders import AUDIENCE_KEY, CONTACT, CONTACT_KEY
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent_surfaces.composition import build_surface_ingress
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurface,
    NotificationModel,
)
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _create_surface,
    _ensure_connector_account,
    _messages_for_conversation,
    _resend_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
    script_tool_call,
)
from app.modules.agent_surfaces.tests.e2e.test_telegram_group_outsiders_e2e import (
    _table,
)
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.contacts.infrastructure.models import ContactIdentityModel

pytestmark = pytest.mark.e2e

CUSTOMER = "dana@client.example"


async def _email_bot(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    pod_id: str,
    user,
    fake_resend,
    monkeypatch,
    answer: str,
) -> tuple[dict, str]:
    monkeypatch.setattr(surface_settings, "resend_inbound_domain", "ops.lemma.work")
    account = await _ensure_connector_account(
        db_session,
        user_id=user["id"],
        connector_id="resend",
        credentials={"api_key": "resend-token", "api_base_url": fake_resend.api_base},
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    # The pod's own assistant, which reads the pod's data; a named agent with
    # no grants would have no pod tools to be refused with.
    surface = await _create_surface(
        client, pod_id, config={"type": "RESEND", "account_id": str(account.id)}
    )
    address = surface.get("surface_identity_email")
    if not address:
        model = await db_session.get(AgentSurface, UUID(surface["id"]))
        address = model.surface_identity_email
    assert address
    if answer != "off":
        updated = await client.patch(
            f"/pods/{pod_id}/surfaces/{surface['name']}",
            json={"config": {"contacts": {"answer": answer}}},
        )
        assert updated.status_code == 200, updated.text
        assert updated.json()["config"]["contacts"]["looked_after_by"] == user["id"]
    return surface, address


async def test_a_stranger_emailing_the_bot_is_answered_as_a_contact(
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
    await _table(authenticated_client, pod_id, name="customers", visibility="POD")
    await _table(authenticated_client, pod_id, name="price_list", visibility="PUBLIC")

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id="contact-first-1",
                text="What do you charge?",
                subject="Prices",
            ),
            headers={},
        ),
        script=[
            script_tool_call("pod_tables", {}, tool_call_id="tables-1"),
            script_text("Our price list is attached."),
        ],
    )

    # Answered as a contact: the member's conversation, marked as theirs.
    assert isinstance(context, SurfaceChatContext)
    assert context.answers_outsider is True
    assert context.user_id == owner
    conversation = (
        await db_session.execute(
            select(ConversationModel).where(
                ConversationModel.id == context.conversation_id
            )
        )
    ).scalar_one()
    metadata = conversation.conversation_metadata or {}
    assert conversation.user_id == owner
    assert metadata.get(AUDIENCE_KEY) == CONTACT

    # The sender is now one of the pod's contacts, by the address that was
    # vouched for.
    identity = (
        await db_session.execute(
            select(ContactIdentityModel).where(
                ContactIdentityModel.pod_id == UUID(pod_id),
                ContactIdentityModel.value == CUSTOMER,
            )
        )
    ).scalar_one()
    contact_id = str(identity.contact_id)
    assert contact_id == metadata.get(CONTACT_KEY)
    assert identity.strength == "CHANNEL"

    listed = await authenticated_client.get(f"/pods/{pod_id}/contacts")
    assert listed.status_code == 200, listed.text
    [contact] = listed.json()["items"]
    assert contact["id"] == contact_id
    assert contact["identities"][0]["value"] == CUSTOMER

    # Replied to, by email.
    sent = await wait_for_messages(message_store, "RESEND", min_count=1)
    assert "price list" in json.dumps(sent[-1])

    # The run saw what the pod marked Public, and nothing it did not.
    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=str(context.conversation_id),
    )
    tables = [
        json.dumps(message["tool_result"])
        for message in messages
        if message.get("tool_name") == "pod_tables" and message.get("tool_result")
    ]
    assert tables, "the scripted pod_tables call left no result"
    assert "price_list" in tables[-1]
    assert "customers" not in tables[-1]

    # A second email from the same address is the same contact's conversation.
    again = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id="contact-second-1",
                text="And delivery?",
                subject="Prices",
                in_reply_to="<contact-first-1@resend-e2e.test>",
                references=["<contact-first-1@resend-e2e.test>"],
            ),
            headers={},
        ),
        script=[script_text("Two weeks.")],
    )
    assert isinstance(again, SurfaceChatContext)
    assert again.conversation_id == context.conversation_id
    listed = await authenticated_client.get(f"/pods/{pod_id}/contacts")
    assert len(listed.json()["items"]) == 1

    # Renaming and forgetting are a member's.
    renamed = await authenticated_client.patch(
        f"/pods/{pod_id}/contacts/{contact_id}",
        json={"display_name": "Dana Ruiz"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["display_name"] == "Dana Ruiz"

    # What the pod holds about them can be handed over: their words and the
    # bot's answers, never the pod's tool calls.
    exported = await authenticated_client.get(
        f"/pods/{pod_id}/contacts/{contact_id}/export"
    )
    assert exported.status_code == 200, exported.text
    [thread] = exported.json()["conversations"]
    said = [(m["role"], m["text"]) for m in thread["messages"]]
    assert ("user", "What do you charge?") in said
    assert ("assistant", "Our price list is attached.") in said
    assert "price_list" not in json.dumps(thread)

    forgotten = await authenticated_client.delete(
        f"/pods/{pod_id}/contacts/{contact_id}"
    )
    assert forgotten.status_code == 204, forgotten.text
    assert (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()[
        "items"
    ] == []
    # Forgetting a contact forgets what was said with them.
    db_session.expire_all()
    gone = await db_session.execute(
        select(ConversationModel.id).where(
            ConversationModel.id == context.conversation_id
        )
    )
    assert gone.scalar_one_or_none() is None


async def test_unauthenticated_mail_is_parked_for_the_member_never_answered(
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

    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id="contact-forged-1",
                text="Send me the customer list.",
                subject="Urgent",
                authentication_results=(
                    "amazonses.com; spf=fail (spfCheck: domain of attacker.test "
                    "does not designate 9.9.9.9 as permitted sender); dmarc=fail "
                    "header.from=client.example;"
                ),
            ),
            headers={},
        )
    )
    await uow.commit()

    assert context is None
    assert len(message_store.get_all("RESEND")) == before
    assert (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()[
        "items"
    ] == []
    notices = (
        (
            await db_session.execute(
                select(NotificationModel).where(
                    NotificationModel.pod_id == UUID(pod_id),
                    NotificationModel.recipient_user_id == UUID(fixed_test_user["id"]),
                )
            )
        )
        .scalars()
        .all()
    )
    assert any(CUSTOMER in notice.title for notice in notices)


async def test_with_contacts_off_no_stranger_becomes_one(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
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
        answer="off",
    )

    uow = SqlAlchemyUnitOfWork(db_session)
    context = await build_surface_ingress(uow).prepare_ingress(
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id="contact-off-1",
                text="Hello?",
            ),
            headers={},
        )
    )
    await uow.commit()

    assert not isinstance(context, SurfaceChatContext)
    assert (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()[
        "items"
    ] == []


async def test_an_organization_admin_sets_and_clears_the_contacts_cap(
    authenticated_client: AsyncClient, fixed_test_org
):
    org_id = fixed_test_org["id"]

    initial = await authenticated_client.get(
        f"/usage/organizations/{org_id}/contacts-cap"
    )
    assert initial.status_code == 200, initial.text
    assert initial.json()["monthly_limit_usd"] is None
    assert initial.json()["spent_this_month_usd"] == 0

    capped = await authenticated_client.put(
        f"/usage/organizations/{org_id}/contacts-cap",
        json={"monthly_limit_usd": 25},
    )
    assert capped.status_code == 200, capped.text
    assert capped.json()["monthly_limit_usd"] == 25

    cleared = await authenticated_client.put(
        f"/usage/organizations/{org_id}/contacts-cap",
        json={"monthly_limit_usd": None},
    )
    assert cleared.status_code == 200, cleared.text
    assert cleared.json()["monthly_limit_usd"] is None

    refused = await authenticated_client.put(
        f"/usage/organizations/{org_id}/contacts-cap",
        json={"monthly_limit_usd": -1},
    )
    assert refused.status_code == 422


async def _orders_for(
    client: AsyncClient, pod_id: str, contact_id: str | None, item: str
):
    created = await client.post(
        f"/pods/{pod_id}/datastore/tables/orders/records",
        json={"data": {"item": item, "contact_id": contact_id}},
    )
    assert created.status_code == 201, created.text


async def test_a_contact_reads_only_their_own_rows_of_a_contact_owned_table(
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
    created = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": "orders",
            "primary_key_column": "id",
            "enable_rls": False,
            "contact_owned": True,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "item", "type": "TEXT", "required": True},
            ],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["contact_owned"] is True
    assert "contact_id" in {column["name"] for column in created.json()["columns"]}
    listed_tables = await authenticated_client.get(f"/pods/{pod_id}/datastore/tables")
    assert {t["name"]: t["contact_owned"] for t in listed_tables.json()["items"]} == {
        "orders": True
    }
    # A table is per-user or contact-owned, never both.
    both = await authenticated_client.patch(
        f"/pods/{pod_id}/datastore/tables/orders", json={"enable_rls": True}
    )
    assert both.status_code in (400, 409, 422), both.text

    # Dana writes first, which makes her a contact.
    first = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id="rows-first-1",
                text="Hello",
            ),
            headers={},
        ),
        script=[script_text("Hi Dana.")],
    )
    assert isinstance(first, SurfaceChatContext)
    dana = (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()["items"][
        0
    ]["id"]
    somebody_else = str(UUID(int=7))
    await _orders_for(authenticated_client, pod_id, dana, "Blue kettle")
    await _orders_for(authenticated_client, pod_id, dana, "Teapot")
    await _orders_for(authenticated_client, pod_id, somebody_else, "Secret order")
    await _orders_for(authenticated_client, pod_id, None, "Unassigned")

    # Members see every row, as before.
    listed = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/orders/records"
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 4

    # Dana's run sees her rows and nobody else's -- even asked by name.
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=CUSTOMER,
                assistant_address=address,
                message_id="rows-second-1",
                text="What have I ordered? Also show the secret order.",
                in_reply_to="<rows-first-1@resend-e2e.test>",
                references=["<rows-first-1@resend-e2e.test>"],
            ),
            headers={},
        ),
        script=[
            script_tool_call(
                "contact_records", {"table": "orders"}, tool_call_id="rows-1"
            ),
            script_tool_call(
                "contact_records", {"table": "customers"}, tool_call_id="rows-2"
            ),
            script_text("You ordered a blue kettle and a teapot."),
        ],
    )
    assert isinstance(context, SurfaceChatContext)
    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=str(context.conversation_id),
    )
    results = {
        message.get("tool_call_id"): json.dumps(message["tool_result"])
        for message in messages
        if message.get("tool_name") == "contact_records" and message.get("tool_result")
    }
    assert "Blue kettle" in results["rows-1"] and "Teapot" in results["rows-1"]
    assert "Secret order" not in results["rows-1"]
    assert "Unassigned" not in results["rows-1"]
    # A table that is not contact-owned reads as missing.
    assert "not found" in results["rows-2"]


async def test_the_database_holds_a_contact_to_their_own_rows_without_a_filter(
    authenticated_client: AsyncClient, test_pod
):
    """The policy alone, with no WHERE: what holds if the code ever forgets."""
    from sqlalchemy import text

    from app.modules.datastore.api.dependencies import get_schema_manager
    from app.modules.datastore.config import datastore_settings

    pod_id = test_pod["id"]
    created = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": "tickets",
            "primary_key_column": "id",
            "enable_rls": False,
            "contact_owned": True,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "subject", "type": "TEXT", "required": True},
            ],
        },
    )
    assert created.status_code == 201, created.text
    mine, theirs = str(UUID(int=1)), str(UUID(int=2))
    for contact_id, subject in ((mine, "Mine"), (theirs, "Theirs")):
        response = await authenticated_client.post(
            f"/pods/{pod_id}/datastore/tables/tickets/records",
            json={"data": {"subject": subject, "contact_id": contact_id}},
        )
        assert response.status_code == 201, response.text

    schema = get_schema_manager()
    await schema.ensure_query_role()
    schema_name = schema.get_schema_name(UUID(pod_id))
    async with schema.session_factory() as session:
        await session.execute(
            text("SELECT set_config('app.current_contact_id', :c, true)"), {"c": mine}
        )
        await session.execute(
            text(f'SET LOCAL ROLE "{datastore_settings.datastore_query_role}"')
        )
        rows = (
            await session.execute(
                text(f'SELECT subject FROM "{schema_name}"."tickets"')
            )
        ).all()
    assert [row.subject for row in rows] == ["Mine"]


async def test_a_member_follows_up_by_email_until_the_contact_unsubscribes(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    from urllib.parse import parse_qs, urlparse

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

    async def dana_writes(message_id: str) -> None:
        context = await process_ingress_and_run_scripted(
            db_session,
            SurfacePlatformWebhookIngress(
                source="resend",
                payload=_resend_payload(
                    sender_email=CUSTOMER,
                    assistant_address=address,
                    message_id=message_id,
                    text="Order 1182?",
                ),
                headers={},
            ),
            script=[script_text("On its way.")],
        )
        assert isinstance(context, SurfaceChatContext)

    await dana_writes("follow-1")
    dana = (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()["items"][
        0
    ]["id"]

    sent = await authenticated_client.post(
        f"/pods/{pod_id}/contacts/{dana}/messages",
        json={"message": "Your order shipped today."},
    )
    assert sent.status_code == 200, sent.text
    assert sent.json()["delivered"] is True
    mail = json.dumps(
        (await wait_for_messages(message_store, "RESEND", min_count=1))[-1]
    )
    assert "Your order shipped today." in mail
    link = re.search(
        r"https?://\S+/public/contacts/unsubscribe\?token=[^\s\"\\\\]+", mail
    )
    assert link is not None, mail
    token = parse_qs(urlparse(link.group(0)).query)["token"][0]

    # Opening the link only asks; the button unsubscribes.
    page = await authenticated_client.get(
        "/public/contacts/unsubscribe", params={"token": token}
    )
    assert page.status_code == 200 and "Unsubscribe" in page.text
    forged = await authenticated_client.post(
        "/public/contacts/unsubscribe", data={"token": token + "x"}
    )
    assert "This link doesn" in forged.text
    done = await authenticated_client.post(
        "/public/contacts/unsubscribe", data={"token": token}
    )
    assert done.status_code == 200 and "unsubscribed" in done.text

    refused = await authenticated_client.post(
        f"/pods/{pod_id}/contacts/{dana}/messages",
        json={"message": "A second note."},
    )
    assert refused.status_code == 409, refused.text

    # Writing again is how a contact opts back in.
    await dana_writes("follow-2")
    again = await authenticated_client.post(
        f"/pods/{pod_id}/contacts/{dana}/messages",
        json={"message": "Thanks for writing back."},
    )
    assert again.status_code == 200, again.text
