"""A web widget, end to end: a public key, a visitor, and the pod's answer.

The whole path with only the model's next move scripted. A member puts a chat
widget on a shop's site; an anonymous visitor writes and is answered from what
the pod made Public; a signed-in customer of the shop, named by a token the
shop's server signed, is answered as a contact; an anonymous visitor who proves
an email address becomes one, keeping the conversation; a visitor held for a
person is answered, once the agent has them back, having read what they said
meanwhile. And the refusals: a
page on another site, a forged or long-lived token, a host session that tries
to outlive its token, a known-only widget's strangers, every guess at a code
counted however fast they come, and a deployment with public web switched off.
"""

from __future__ import annotations

from dataclasses import dataclass

import asyncio
import base64
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api.dependencies import get_uow_factory
from app.core.config import settings
from app.core.public_web import public_web_settings
from app.modules.datastore.events.contact_merged_work import (
    register_contact_merged_work as register_datastore_contact_merged_work,
)
from app.modules.agent.domain.folded_turns import FOLDED_CHAT_ENDS, FOLDED_CHAT_OPENS
from app.modules.agent.infrastructure.models import AgentRunModel, ConversationModel
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent_surfaces.events.visitor_retention import sweep_web_visitors
from app.modules.agent_surfaces.services.channel_deliveries import WAITS_IN_WEB_CHAT
from app.modules.agent_surfaces.services.channel_lines import web_place
from app.modules.channels.contracts import ContactsHome, source_for
from app.modules.channels.infrastructure.models import ChannelSourceModel
from app.modules.channels.infrastructure.source_repository import MAX_PLACES
from app.modules.agent_surfaces.tests.e2e.helpers import _messages_for_conversation
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    record_model_requests,
    run_scripted_agent_run,
    script_text,
    script_tool_call,
)
from app.modules.agent_surfaces.tests.e2e.surface_work_support import (
    contact_channel_id,
    send_deliveries,
    deliveries_of_line,
    member_writes,
)
from app.modules.agent_surfaces.tests.e2e.test_telegram_group_outsiders_e2e import (
    _table,
)
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass, WorkRegistry
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.agent.events.contact_merged_work import register_contact_merged_work
from app.modules.channels.events.contact_merged_work import (
    register_contact_merged_work as register_channels_contact_merged_work,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.contacts.contracts import IdentityKind, IdentityStrength, open_contact
from app.modules.contacts.contracts.outreach import SuppressionReason, opt_out
from app.modules.contacts.infrastructure.models import (
    ContactIdentityModel,
    ContactModel,
)
from app.modules.contacts.infrastructure.visitor_sessions import (
    VisitorCodeModel,
    VisitorSessionModel,
)
from app.modules.test_support.e2e.scripted_model import script_thinking
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.query_counting import (
    counted_queries,
    format_statements,
    statements_touching,
)

pytestmark = pytest.mark.e2e

# One user and organization for the whole module, reset to exactly their rows
# before each test; each test isolates itself with the pod it creates.
module_identity = e2e_fixtures.module_identity
fixed_test_user = e2e_fixtures.module_test_user
fixed_test_org = e2e_fixtures.module_test_org

SHOP = "https://shop.example"


def _page() -> dict[str, str]:
    """A request from the shop's page with no session: no member's token either."""
    return {"Origin": SHOP, "Authorization": ""}


def _as(visitor: dict) -> dict[str, str]:
    """A request from the shop's page, carrying the visitor's access token."""
    return {"Origin": SHOP, "Authorization": f"Bearer {visitor['access_token']}"}


async def _widget(client: AsyncClient, pod_id: str, **overrides) -> dict:
    created = await client.post(
        f"/pods/{pod_id}/web-widgets",
        json={
            "name": "Shop chat",
            "allowed_origins": [SHOP],
            "answer": "anyone",
            **overrides,
        },
    )
    assert created.status_code == 201, created.text
    return created.json()


async def _session(client: AsyncClient, key: str, **body) -> dict:
    started = await client.post(
        f"/public/web/{key}/session", json=body, headers=_page()
    )
    assert started.status_code == 200, started.text
    return started.json()


async def _conversation_of(db_session: AsyncSession, visitor: dict) -> UUID | None:
    db_session.expire_all()
    return (
        await db_session.execute(
            select(VisitorSessionModel.conversation_id).where(
                VisitorSessionModel.id == _session_id(visitor)
            )
        )
    ).scalar_one()


def _session_id(visitor: dict) -> UUID:
    from app.modules.agent_surfaces.services.visitor_access import read_visitor_access

    return read_visitor_access(visitor["access_token"]).session_id


async def _say(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    key: str,
    visitor: dict,
    text: str,
    owner: UUID,
    pod_id: str,
    script: list,
    client_nonce: str | None = None,
) -> UUID:
    body: dict[str, str] = {"text": text}
    if client_nonce is not None:
        body["client_nonce"] = client_nonce
    with suppress_agent_run_enqueue():
        sent = await client.post(
            f"/public/web/{key}/messages", json=body, headers=_as(visitor)
        )
    assert sent.status_code == 202, sent.text
    conversation_id = await _conversation_of(db_session, visitor)
    assert conversation_id is not None
    await run_scripted_agent_run(
        db_session,
        conversation_id=conversation_id,
        user_id=owner,
        pod_id=UUID(pod_id),
        agent_name="pod_default",
        script=script,
    )
    return conversation_id


async def _contact_of(db_session: AsyncSession, conversation_id: UUID) -> UUID | None:
    """The contact a conversation answers: its column, which only the server writes."""
    db_session.expire_all()
    conversation = await db_session.get(ConversationModel, conversation_id)
    assert conversation is not None
    return conversation.contact_id


async def _handles_of(
    db_session: AsyncSession, contact_id: UUID
) -> set[tuple[str, str]]:
    db_session.expire_all()
    rows = await db_session.execute(
        select(ContactIdentityModel.kind, ContactIdentityModel.strength).where(
            ContactIdentityModel.contact_id == contact_id
        )
    )
    return {(kind, strength) for kind, strength in rows}


def _host_token(secret: str, key: str, *, subject: str, lifetime: int = 300) -> str:
    return jwt.encode(
        {"sub": subject, "aud": key, "exp": int(time.time()) + lifetime, "name": "Ana"},
        secret,
        algorithm="HS256",
    )


async def test_an_anonymous_visitor_is_answered_from_what_is_public(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    widget = await _widget(authenticated_client, pod_id)
    key = widget["public_key"]
    assert widget["signing_secret"].startswith("sk_")
    assert key in widget["embed"]
    listed = await authenticated_client.get(f"/pods/{pod_id}/web-widgets")
    assert "signing_secret" not in json.dumps(listed.json())

    await _table(authenticated_client, pod_id, name="customers", visibility="POD")
    await _table(authenticated_client, pod_id, name="price_list", visibility="PUBLIC")

    # A page on another site is refused, and is not told anything it can read.
    elsewhere = await authenticated_client.post(
        f"/public/web/{key}/session",
        json={},
        headers={"Origin": "https://evil.example", "Authorization": ""},
    )
    assert elsewhere.status_code == 403
    assert "access-control-allow-origin" not in elsewhere.headers

    started = await authenticated_client.post(
        f"/public/web/{key}/session", json={}, headers=_page()
    )
    assert started.status_code == 200, started.text
    assert started.headers["access-control-allow-origin"] == SHOP
    assert "access-control-allow-credentials" not in started.headers
    visitor = started.json()
    assert visitor["is_contact"] is False
    assert visitor["secret"].startswith("vs_")
    assert visitor["expires_in"] == 900

    conversation_id = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="What do you sell?",
        owner=UUID(fixed_test_user["id"]),
        pod_id=pod_id,
        script=[
            script_tool_call("pod_tables", {}, tool_call_id="tables-1"),
            # The model's thinking is the pod's working, never the visitor's.
            script_thinking(
                "A visitor; only Public tables apply.", "Here is our price list."
            ),
        ],
    )
    # Answered as a contact nobody has vouched for: known by the browser alone.
    visitor_contact = await _contact_of(db_session, conversation_id)
    assert visitor_contact is not None
    assert await _handles_of(db_session, visitor_contact) == {("WEB", "SESSION")}

    history = await authenticated_client.get(
        f"/public/web/{key}/history", headers=_as(visitor)
    )
    assert history.status_code == 200, history.text
    said = [(m["role"], m["text"]) for m in history.json()["messages"]]
    assert said == [
        ("user", "What do you sell?"),
        ("assistant", "Here is our price list."),
    ]

    # Both sides are lines of the visitor's contact channel, on its web chat,
    # the answer said for them -- the timeline the pod's members read.
    listed = await authenticated_client.get(
        f"/pods/{pod_id}/channels", params={"kind": "CONTACT"}
    )
    [channel] = [
        c
        for c in listed.json()["items"]
        if (c["person"] or {}).get("id") == str(visitor_contact)
    ]
    timeline = await authenticated_client.get(
        f"/pods/{pod_id}/channels/{channel['id']}/messages"
    )
    lines = timeline.json()["items"]
    assert [(line["sender"]["kind"], line["text"]) for line in lines] == [
        ("CONTACT", "What do you sell?"),
        ("AGENT", "Here is our price list."),
    ]
    assert lines[1]["on_behalf_of"] == {
        "kind": "CONTACT",
        "id": str(visitor_contact),
        "name": None,
    }
    assert len({line["source_id"] for line in lines}) == 1

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=str(conversation_id)
    )
    tables = [
        json.dumps(m["tool_result"])
        for m in messages
        if m.get("tool_name") == "pod_tables" and m.get("tool_result")
    ]
    assert "price_list" in tables[-1] and "customers" not in tables[-1]

    # Somebody else's token reads nothing, and is told so in a way the page
    # can read.
    stranger = await authenticated_client.get(
        f"/public/web/{key}/history",
        headers={"Origin": SHOP, "Authorization": "Bearer not-a-token"},
    )
    assert stranger.status_code == 401
    assert stranger.json()["code"] == "bad_token"
    assert stranger.headers["access-control-allow-origin"] == SHOP

    # The secret brings the visitor back to the same chat, without a new one.
    back = await _session(authenticated_client, key, secret=visitor["secret"])
    assert back["secret"] is None
    assert _session_id(back) == _session_id(visitor)


async def test_a_signed_in_customer_of_the_shop_is_answered_as_a_contact(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    widget = await _widget(authenticated_client, pod_id)
    key, secret = widget["public_key"], widget["signing_secret"]

    # A token signed with anything else, or meant to live for an hour, is refused.
    for bad in (
        _host_token("sk_wrong", key, subject="cust-42"),
        _host_token(secret, key, subject="cust-42", lifetime=3600),
        _host_token(secret, "pk_other", subject="cust-42"),
        _host_token(secret, key, subject="c" * 201),
    ):
        refused = await authenticated_client.post(
            f"/public/web/{key}/session", json={"host_token": bad}, headers=_page()
        )
        assert refused.status_code == 401, refused.text

    visitor = await _session(
        authenticated_client,
        key,
        host_token=_host_token(secret, key, subject="cust-42"),
    )
    assert visitor["is_contact"] is True
    assert visitor["display_name"] == "Ana"

    conversation_id = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="Where is my order?",
        owner=UUID(fixed_test_user["id"]),
        pod_id=pod_id,
        script=[script_text("Let me check.")],
    )
    contact_id = await _contact_of(db_session, conversation_id)
    identity = (
        await db_session.execute(
            select(ContactIdentityModel).where(
                ContactIdentityModel.pod_id == UUID(pod_id),
                ContactIdentityModel.kind == "HOST",
            )
        )
    ).scalar_one()
    # Keyed by the pod and the product the widget speaks for, so another of
    # its widgets for that product meets the same contact.
    assert widget["host_issuer"] == "default"
    assert identity.value == "host:default:cust-42"
    assert identity.issuer == "default"
    assert identity.strength == "HOST"
    assert identity.contact_id == contact_id

    other = await _widget(authenticated_client, pod_id, name="Help centre")
    again = await _session(
        authenticated_client,
        other["public_key"],
        host_token=_host_token(
            other["signing_secret"], other["public_key"], subject="cust-42"
        ),
    )
    assert again["is_contact"] is True
    db_session.expire_all()
    assert (
        await db_session.execute(
            select(ContactIdentityModel).where(
                ContactIdentityModel.pod_id == UUID(pod_id),
                ContactIdentityModel.kind == "HOST",
            )
        )
    ).scalars().all() == [identity]

    # Another product's user 42 is somebody else.
    elsewhere = await _widget(
        authenticated_client, pod_id, name="Partner app", host_issuer="Partner-App"
    )
    assert elsewhere["host_issuer"] == "partner-app"
    partner = await _session(
        authenticated_client,
        elsewhere["public_key"],
        host_token=_host_token(
            elsewhere["signing_secret"], elsewhere["public_key"], subject="cust-42"
        ),
    )
    assert partner["is_contact"] is True
    db_session.expire_all()
    hosts = (
        await db_session.execute(
            select(ContactIdentityModel.value, ContactIdentityModel.contact_id).where(
                ContactIdentityModel.pod_id == UUID(pod_id),
                ContactIdentityModel.kind == "HOST",
            )
        )
    ).all()
    assert {value for value, _ in hosts} == {
        "host:default:cust-42",
        "host:partner-app:cust-42",
    }
    assert len({contact for _, contact in hosts}) == 2
    unnamed = await authenticated_client.post(
        f"/pods/{pod_id}/web-widgets",
        json={"name": "Bad issuer", "host_issuer": "two words"},
    )
    assert unnamed.status_code == 422, unnamed.text

    # Rotating the secret retires every token signed with the old one, and the
    # sessions they opened.
    rotated = await authenticated_client.post(
        f"/pods/{pod_id}/web-widgets/{widget['id']}/secret"
    )
    assert rotated.status_code == 200, rotated.text
    stale = await authenticated_client.post(
        f"/public/web/{key}/session",
        json={"host_token": _host_token(secret, key, subject="cust-42")},
        headers=_page(),
    )
    assert stale.status_code == 401
    ended = await authenticated_client.get(
        f"/public/web/{key}/history", headers=_as(visitor)
    )
    assert ended.status_code == 401
    assert ended.json()["code"] == "no_session"


async def test_a_host_session_lives_only_as_long_as_the_hosts_tokens(
    authenticated_client: AsyncClient, test_pod
):
    widget = await _widget(authenticated_client, test_pod["id"], name="Accounts")
    key, secret = widget["public_key"], widget["signing_secret"]
    visitor = await _session(
        authenticated_client, key, host_token=_host_token(secret, key, subject="u-1")
    )
    assert visitor["secret"]

    # The secret alone would let a ten-minute token mint a session forever.
    alone = await authenticated_client.post(
        f"/public/web/{key}/session",
        json={"secret": visitor["secret"]},
        headers=_page(),
    )
    assert alone.status_code == 401
    assert alone.json()["code"] == "host_token_required"

    fresh = await _session(
        authenticated_client,
        key,
        secret=visitor["secret"],
        host_token=_host_token(secret, key, subject="u-1"),
    )
    assert _session_id(fresh) == _session_id(visitor)
    assert fresh["secret"] is None

    # Somebody else signing in on the same page gets a session of their own.
    someone = await _session(
        authenticated_client,
        key,
        secret=visitor["secret"],
        host_token=_host_token(secret, key, subject="u-2"),
    )
    assert someone["secret"] and _session_id(someone) != _session_id(visitor)


def _latest_code(email: str) -> str:
    spool = sorted(
        Path(settings.email_output_dir).glob(f"*{email.replace('@', '_at_')}*")
    )
    assert spool, "no code was sent"
    body = json.loads(spool[-1].read_text())
    match = re.search(r"\b(\d{6})\b", body["subject"])
    assert match is not None
    return match.group(1)


def _latest_mail(email: str) -> dict:
    spool = sorted(
        Path(settings.email_output_dir).glob(f"*{email.replace('@', '_at_')}*")
    )
    return json.loads(spool[-1].read_text())


async def test_a_visitor_who_proves_an_email_becomes_a_contact_in_the_same_chat(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    widget = await _widget(authenticated_client, pod_id)
    key = widget["public_key"]
    visitor = await _session(authenticated_client, key)
    conversation_id = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="Hi",
        owner=UUID(fixed_test_user["id"]),
        pod_id=pod_id,
        script=[script_text("Hello!")],
    )
    anonymous = await _contact_of(db_session, conversation_id)
    assert anonymous is not None
    assert await _handles_of(db_session, anonymous) == {("WEB", "SESSION")}

    email = f"visitor-{widget['id'][:8]}@client.example"
    sent = await authenticated_client.post(
        f"/public/web/{key}/code", json={"email": email}, headers=_as(visitor)
    )
    assert sent.status_code == 200, sent.text
    mail = _latest_mail(email)
    assert mail["from_name"] == "Shop chat via Lemma"
    assert "Lemma sent this on behalf of" in mail["text_content"]
    assert mail["subject"] != "Shop chat"

    wrong = await authenticated_client.post(
        f"/public/web/{key}/code/verify",
        json={"email": email, "code": "000000x"},
        headers=_as(visitor),
    )
    assert wrong.status_code == 400
    verified = await authenticated_client.post(
        f"/public/web/{key}/code/verify",
        json={"email": email, "code": _latest_code(email)},
        headers=_as(visitor),
    )
    assert verified.status_code == 200, verified.text
    contact = verified.json()
    assert contact["is_contact"] is True
    # A contact's session is the old one under a new secret: whoever copied the
    # anonymous one does not hold a contact's.
    assert contact["secret"] and contact["secret"] != visitor["secret"]
    assert _session_id(contact) == _session_id(visitor)
    old = await authenticated_client.post(
        f"/public/web/{key}/session",
        json={"secret": visitor["secret"]},
        headers=_page(),
    )
    assert old.status_code == 401
    assert (await _session(authenticated_client, key, secret=contact["secret"]))[
        "is_contact"
    ]

    # The address joined the contact they already were: the same conversation,
    # the same contact, confirmed now.
    assert await _contact_of(db_session, conversation_id) == anonymous
    assert await _handles_of(db_session, anonymous) == {
        ("WEB", "SESSION"),
        ("EMAIL", "CODE"),
    }

    # One session is one person: another address starts another chat.
    another = await authenticated_client.post(
        f"/public/web/{key}/code",
        json={"email": "someone-else@client.example"},
        headers=_as(contact),
    )
    assert another.status_code == 409
    assert another.json()["code"] == "already_a_contact"


async def test_every_guess_at_a_code_counts_however_fast_they_come(
    authenticated_client: AsyncClient, db_session: AsyncSession, test_pod
):
    widget = await _widget(authenticated_client, test_pod["id"], name="Guesses")
    key = widget["public_key"]
    visitor = await _session(authenticated_client, key)
    email = f"guess-{widget['id'][:8]}@client.example"
    sent = await authenticated_client.post(
        f"/public/web/{key}/code", json={"email": email}, headers=_as(visitor)
    )
    assert sent.status_code == 200, sent.text
    code = _latest_code(email)
    wrong = f"{(int(code) + 1) % 1_000_000:06d}"

    guesses = await asyncio.gather(
        *(
            authenticated_client.post(
                f"/public/web/{key}/code/verify",
                json={"email": email, "code": wrong},
                headers=_as(visitor),
            )
            for _ in range(5)
        )
    )

    assert [guess.status_code for guess in guesses] == [400] * 5
    db_session.expire_all()
    attempts = (
        await db_session.execute(
            select(VisitorCodeModel.attempts).where(
                VisitorCodeModel.session_id == _session_id(visitor)
            )
        )
    ).scalar_one()
    assert attempts == 5
    # The code is used up: even the right one is refused now.
    late = await authenticated_client.post(
        f"/public/web/{key}/code/verify",
        json={"email": email, "code": code},
        headers=_as(visitor),
    )
    assert late.status_code == 400
    assert "expired" in late.json()["message"]


async def test_a_known_only_widget_refuses_strangers(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    known = await _widget(authenticated_client, pod_id, name="Members", answer="known")
    visitor = await _session(authenticated_client, known["public_key"])
    refused = await authenticated_client.post(
        f"/public/web/{known['public_key']}/messages",
        json={"text": "Hello?"},
        headers=_as(visitor),
    )
    assert refused.status_code == 403

    off = await authenticated_client.patch(
        f"/pods/{pod_id}/web-widgets/{known['id']}", json={"answer": "off"}
    )
    assert off.status_code == 200, off.text
    gone = await authenticated_client.post(
        f"/public/web/{known['public_key']}/session", json={}, headers=_page()
    )
    assert gone.status_code == 404


async def test_a_message_too_long_is_refused_not_cut(
    authenticated_client: AsyncClient, test_pod
):
    widget = await _widget(authenticated_client, test_pod["id"], name="Long")
    visitor = await _session(authenticated_client, widget["public_key"])

    refused = await authenticated_client.post(
        f"/public/web/{widget['public_key']}/messages",
        json={"text": "x" * 4001},
        headers=_as(visitor),
    )

    assert refused.status_code == 422


async def test_a_page_knows_its_own_message_by_the_name_it_gave_it(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    widget = await _widget(authenticated_client, pod_id, name="Nonce")
    key, secret = widget["public_key"], widget["signing_secret"]
    visitor = await _session(
        authenticated_client,
        key,
        host_token=_host_token(secret, key, subject="cust-nonce"),
    )

    too_long = await authenticated_client.post(
        f"/public/web/{key}/messages",
        json={"text": "Hi", "client_nonce": "n" * 65},
        headers=_as(visitor),
    )
    assert too_long.status_code == 422

    conversation_id = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="Where is my order?",
        owner=UUID(fixed_test_user["id"]),
        pod_id=pod_id,
        script=[script_text("Let me check.")],
        client_nonce="n-1",
    )

    # The page's name comes back on the visitor's message, and on nothing else.
    history = await authenticated_client.get(
        f"/public/web/{key}/history", headers=_as(visitor)
    )
    assert history.status_code == 200, history.text
    assert [(m["role"], m["client_nonce"]) for m in history.json()["messages"]] == [
        ("user", "n-1"),
        ("assistant", None),
    ]

    # It is the page's bookkeeping, not what was said: an export leaves it out.
    contact_id = await _contact_of(db_session, conversation_id)
    exported = await authenticated_client.get(
        f"/pods/{pod_id}/contacts/{contact_id}/export"
    )
    assert exported.status_code == 200, exported.text
    assert "client_nonce" not in json.dumps(exported.json())


async def test_a_new_widget_answers_nobody_and_names_only_real_origins(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    quiet = await authenticated_client.post(
        f"/pods/{pod_id}/web-widgets", json={"name": "Quiet"}
    )
    assert quiet.status_code == 201, quiet.text
    assert quiet.json()["answer"] == "off"

    for origin in ("https://shop.example/checkout", "http://shop.example", "shop"):
        refused = await authenticated_client.post(
            f"/pods/{pod_id}/web-widgets",
            json={"name": f"Bad {origin}", "allowed_origins": [origin]},
        )
        assert refused.status_code == 422, origin
    named = await _widget(
        authenticated_client,
        pod_id,
        name="Local",
        allowed_origins=["http://localhost:5173", "https://Shop.example:8443/"],
    )
    assert named["allowed_origins"] == [
        "http://localhost:5173",
        "https://shop.example:8443",
    ]


async def test_a_widget_name_the_pod_already_has_is_refused_not_a_server_error(
    authenticated_client: AsyncClient, test_pod, fixed_test_org
):
    """One name is one widget in a pod; another pod may use it."""
    pod_id = test_pod["id"]
    first = await _widget(authenticated_client, pod_id, name="Shop chat")

    again = await authenticated_client.post(
        f"/pods/{pod_id}/web-widgets", json={"name": "  Shop chat "}
    )

    assert again.status_code == 409, again.text
    assert again.json()["code"] == "widget_name_taken"
    assert "INSERT" not in again.text and "lsenc" not in again.text
    listed = (await authenticated_client.get(f"/pods/{pod_id}/web-widgets")).json()
    assert [w["id"] for w in listed["items"] if w["name"] == "Shop chat"] == [
        first["id"]
    ]
    other = await authenticated_client.post(
        "/pods",
        json={
            "organization_id": fixed_test_org["id"],
            "name": f"Other shop {uuid4().hex[:6]}",
        },
    )
    assert other.status_code == 201, other.text
    await _widget(authenticated_client, other.json()["id"], name="Shop chat")


async def test_nothing_is_served_while_public_web_is_switched_off(
    authenticated_client: AsyncClient, test_pod, monkeypatch
):
    pod_id = test_pod["id"]
    widget = await _widget(authenticated_client, pod_id, name="Switched")
    key = widget["public_key"]
    monkeypatch.setattr(public_web_settings, "public_web_enabled", False)

    for path in (
        "/public/web/widget.js",
        f"/public/web/{key}/page",
        f"/public/web/{key}/challenge",
    ):
        assert (
            await authenticated_client.get(path, headers=_page())
        ).status_code == 404
    started = await authenticated_client.post(
        f"/public/web/{key}/session", json={}, headers=_page()
    )
    assert started.status_code == 404
    preflight = await authenticated_client.options(
        f"/public/web/{key}/session",
        headers={"Origin": SHOP, "Access-Control-Request-Method": "POST"},
    )
    assert "access-control-allow-origin" not in preflight.headers

    answering = await authenticated_client.post(
        f"/pods/{pod_id}/web-widgets", json={"name": "Eager", "answer": "anyone"}
    )
    assert answering.status_code == 409
    switched_on = await authenticated_client.patch(
        f"/pods/{pod_id}/web-widgets/{widget['id']}", json={"answer": "known"}
    )
    assert switched_on.status_code == 409
    quiet = await authenticated_client.post(
        f"/pods/{pod_id}/web-widgets", json={"name": "Quiet"}
    )
    assert quiet.status_code == 201


async def test_a_pages_preflight_is_answered_from_its_widget(
    authenticated_client: AsyncClient, test_pod
):
    widget = await _widget(authenticated_client, test_pod["id"], name="Preflight")
    key = widget["public_key"]

    def preflight(origin: str):
        return authenticated_client.options(
            f"/public/web/{key}/messages",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization, content-type",
            },
        )

    allowed = await preflight(SHOP)
    assert allowed.status_code == 204
    assert allowed.headers["access-control-allow-origin"] == SHOP
    assert allowed.headers["access-control-max-age"] == "7200"
    refused = await preflight("https://evil.example")
    assert refused.status_code == 400
    for answer in (allowed, refused):
        assert "access-control-allow-credentials" not in answer.headers
    assert "access-control-allow-origin" not in refused.headers

    # A member changing the widget's origins is honoured on the next request.
    changed = await authenticated_client.patch(
        f"/pods/{test_pod['id']}/web-widgets/{widget['id']}",
        json={"allowed_origins": ["https://other.example"]},
    )
    assert changed.status_code == 200, changed.text
    assert (await preflight(SHOP)).status_code == 400


async def test_the_widget_script_is_served_without_a_session(async_client: AsyncClient):
    script = await async_client.get("/public/web/widget.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert "data-lemma-key" in script.text


async def test_the_live_stream_needs_a_session_and_a_conversation(
    authenticated_client: AsyncClient, test_pod
):
    widget = await _widget(authenticated_client, test_pod["id"], name="Streams")
    key = widget["public_key"]
    visitor = await _session(authenticated_client, key)

    stranger = await authenticated_client.get(
        f"/public/web/{key}/stream",
        headers={"Origin": SHOP, "Authorization": "Bearer not-a-token"},
    )
    assert stranger.status_code == 401

    # Nothing has been said yet, so there is nothing to watch: no subscription
    # is held open for a visitor who has only opened the page.
    quiet = await authenticated_client.get(
        f"/public/web/{key}/stream", headers=_as(visitor)
    )
    assert quiet.status_code == 204
    assert quiet.headers["access-control-allow-origin"] == SHOP
    assert visitor["title"]


@pytest.mark.drives_work
async def test_a_members_line_waits_in_a_web_contacts_chat(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    widget = await _widget(authenticated_client, pod_id, name="Follow-ups")
    key, secret = widget["public_key"], widget["signing_secret"]
    visitor = await _session(
        authenticated_client, key, host_token=_host_token(secret, key, subject="cust-7")
    )
    await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="Is the blue one back in stock?",
        owner=UUID(fixed_test_user["id"]),
        pod_id=pod_id,
        script=[script_text("I'll check.")],
    )
    contact = (await authenticated_client.get(f"/pods/{pod_id}/contacts")).json()[
        "items"
    ][0]["id"]

    # A member writes in their channel; there is nobody to hand it to, so it
    # waits in their web chat for their next visit.
    channel = await contact_channel_id(authenticated_client, pod_id, contact)
    # The member looking after the widget hears of a new contact without
    # joining their channel.
    shown = await authenticated_client.get(f"/pods/{pod_id}/channels/{channel}")
    assert shown.json()["joined"] is True, shown.text
    assert shown.json()["unread"]["lines"] >= 1, shown.text
    written = await member_writes(
        authenticated_client, pod_id, channel, "It's back in stock."
    )
    await send_deliveries()
    [delivery] = await deliveries_of_line(written["id"])
    assert (delivery.platform, delivery.status.value) == ("WEB", "SENT")
    # Said as it is: in their chat, not in front of them.
    assert delivery.reason == WAITS_IN_WEB_CHAT
    history = await authenticated_client.get(
        f"/public/web/{key}/history", headers=_as(visitor)
    )
    # A person wrote it, and the visitor's chat can say so; the bot's own
    # answer is the bot's.
    said = [
        (m["role"], m["text"], m["from_person"]) for m in history.json()["messages"]
    ]
    assert ("assistant", "It's back in stock.", True) in said
    assert ("assistant", "I'll check.", False) in said

    # Forgetting the contact ends the session that named them.
    forgotten = await authenticated_client.delete(f"/pods/{pod_id}/contacts/{contact}")
    assert forgotten.status_code == 204, forgotten.text
    after = await authenticated_client.get(
        f"/public/web/{key}/history", headers=_as(visitor)
    )
    assert after.status_code == 401


async def _write_first(
    client: AsyncClient,
    pod_id: str,
    contact_id: UUID,
    handle: str,
    kind: str,
    text: str,
):
    return await client.post(
        f"/pods/{pod_id}/contacts/{contact_id}/outreach",
        json={"handle": handle, "kind": kind, "text": text},
    )


@pytest.mark.drives_work
async def test_a_member_writes_first_to_a_visitor_who_left_no_address(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """A lead who chatted on the site and never confirmed an email is written
    to the next day: the line waits in their web chat, and they read it when
    they come back with the secret their page kept."""
    pod_id = test_pod["id"]
    owner = UUID(fixed_test_user["id"])
    widget = await _widget(authenticated_client, pod_id, name="Leads")
    key = widget["public_key"]
    visitor = await _session(authenticated_client, key)
    conversation_id = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="Do you deliver to Leeds?",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("Let me find out.")],
    )
    contact = await _contact_of(db_session, conversation_id)
    assert contact is not None
    browser = str(_session_id(visitor))
    text = f"We do -- next-day to Leeds [{uuid4().hex[:6]}]"

    reached = await _write_first(
        authenticated_client, pod_id, contact, browser, "WEB", text
    )

    assert reached.status_code == 201, reached.text
    line, delivery = reached.json()["line"], reached.json()["delivery"]
    assert delivery["status"] == "PENDING"
    timeline = await authenticated_client.get(
        f"/pods/{pod_id}/channels/{line['channel_id']}/messages"
    )
    [said] = [item for item in timeline.json()["items"] if item["id"] == line["id"]]
    assert said["sender"]["kind"] == "MEMBER"
    assert said["source_id"] == delivery["source_id"]
    await send_deliveries()
    [row] = await deliveries_of_line(line["id"])
    assert (row.platform, row.status.value) == ("WEB", "SENT"), row.reason

    # Their next visit: the secret their page kept, a new access token, and
    # the line in their chat.
    back = await _session(authenticated_client, key, secret=visitor["secret"])
    assert _session_id(back) == _session_id(visitor)
    history = await authenticated_client.get(
        f"/public/web/{key}/history", headers=_as(back)
    )
    assert history.status_code == 200, history.text
    assert [(m["role"], m["text"]) for m in history.json()["messages"]][-1] == (
        "assistant",
        text,
    )

    # Opt-out first, whatever else holds: a browser that asked not to be
    # written to is not, and nothing is posted.
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await opt_out(
            uow,
            pod_id=UUID(pod_id),
            kind=IdentityKind.WEB,
            value=browser,
            reason=SuppressionReason.STOP,
        )
        await uow.commit()
    stopped = await _write_first(
        authenticated_client, pod_id, contact, browser, "WEB", "One more thing"
    )
    assert stopped.status_code == 409, stopped.text

    # A chat that has ended is never opened again, so nobody would read it.
    other = await _session(authenticated_client, key)
    await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=other,
        text="Hello?",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("Hi.")],
    )
    gone = await _contact_of(db_session, await _conversation_of(db_session, other))
    await db_session.execute(
        update(VisitorSessionModel)
        .where(VisitorSessionModel.id == _session_id(other))
        .values(expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
    )
    await db_session.commit()
    ended = await _write_first(
        authenticated_client,
        pod_id,
        gone,
        str(_session_id(other)),
        "WEB",
        "Still there?",
    )
    assert ended.status_code == 422, ended.text
    assert "no chat on your website" in ended.json()["message"]
    gone_channel = await contact_channel_id(authenticated_client, pod_id, str(gone))
    unwritten = await authenticated_client.get(
        f"/pods/{pod_id}/channels/{gone_channel}/messages"
    )
    assert "Still there?" not in [item["text"] for item in unwritten.json()["items"]]
    # Opt-out is read before any chat is looked for: an ended chat whose
    # browser asked not to be written to is refused as opted out.
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        await opt_out(
            uow,
            pod_id=UUID(pod_id),
            kind=IdentityKind.WEB,
            value=str(_session_id(other)),
            reason=SuppressionReason.STOP,
        )
        await uow.commit()
    refused = await _write_first(
        authenticated_client,
        pod_id,
        gone,
        str(_session_id(other)),
        "WEB",
        "Still there?",
    )
    assert refused.status_code == 409, refused.text


@pytest.mark.drives_work
async def test_a_member_writes_first_to_a_customer_the_site_signed_in(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """Named by the shop's token, they are written to at their latest chat on
    a widget for that shop, and read it when they come back signed in."""
    pod_id = test_pod["id"]
    widget = await _widget(authenticated_client, pod_id, name="Account help")
    key, secret = widget["public_key"], widget["signing_secret"]
    visitor = await _session(
        authenticated_client, key, host_token=_host_token(secret, key, subject="cust-9")
    )
    conversation_id = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="Can I change my plan?",
        owner=UUID(fixed_test_user["id"]),
        pod_id=pod_id,
        script=[script_text("I'll ask the team.")],
    )
    contact = await _contact_of(db_session, conversation_id)
    assert contact is not None
    # More chats than a channel's places are read at once, all heard from
    # since and all ended: none of them hides the one that is still live.
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        for _ in range(MAX_PLACES + 5):
            await source_for(
                uow,
                pod_id=UUID(pod_id),
                place=web_place(widget_id=UUID(widget["id"]), session_id=uuid4()),
                home=ContactsHome(contact_id=contact),
            )
        await session.execute(
            update(ChannelSourceModel)
            .where(
                ChannelSourceModel.platform == "WEB",
                ChannelSourceModel.external_user_id != str(_session_id(visitor)),
            )
            .values(last_inbound_at=datetime.now(timezone.utc) + timedelta(minutes=5))
        )
        await uow.commit()

    reached = await _write_first(
        authenticated_client,
        pod_id,
        contact,
        "host:default:cust-9",
        "HOST",
        "Yes -- it's done from Settings, Plan.",
    )

    assert reached.status_code == 201, reached.text
    line = reached.json()["line"]
    await send_deliveries()
    [row] = await deliveries_of_line(line["id"])
    assert (row.platform, row.status.value) == ("WEB", "SENT"), row.reason
    back = await _session(
        authenticated_client,
        key,
        secret=visitor["secret"],
        host_token=_host_token(secret, key, subject="cust-9"),
    )
    history = await authenticated_client.get(
        f"/public/web/{key}/history", headers=_as(back)
    )
    assert ("assistant", "Yes -- it's done from Settings, Plan.") in [
        (m["role"], m["text"]) for m in history.json()["messages"]
    ]


async def test_the_sweep_clears_ended_sessions_and_keeps_what_visitors_said(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """A session is a credential and goes when it ends; a conversation is a
    contact's, anonymous or not, and stays until a member deletes it."""
    pod_id = test_pod["id"]
    widget = await _widget(authenticated_client, pod_id, name="Sweep")
    key = widget["public_key"]
    gone_by = await _session(authenticated_client, key)
    kept = await _session(authenticated_client, key)
    idle_conversation = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=gone_by,
        text="Anyone there?",
        owner=UUID(fixed_test_user["id"]),
        pod_id=pod_id,
        script=[script_text("Yes!")],
    )
    long_ago = datetime.now(timezone.utc) - timedelta(days=91)
    await db_session.execute(
        update(VisitorSessionModel)
        .where(VisitorSessionModel.id == _session_id(gone_by))
        .values(expires_at=long_ago)
    )
    await db_session.execute(
        update(ConversationModel)
        .where(ConversationModel.id == idle_conversation)
        .values(last_activity_at=long_ago)
    )
    await db_session.commit()

    codes, sessions = await sweep_web_visitors(get_uow_factory())

    assert sessions >= 1 and codes >= 0
    db_session.expire_all()
    remaining = set(
        (
            await db_session.execute(
                select(VisitorSessionModel.id).where(
                    VisitorSessionModel.widget_id == UUID(widget["id"])
                )
            )
        ).scalars()
    )
    assert remaining == {_session_id(kept)}
    # What they said, and who they were, outlive the session.
    assert await db_session.get(ConversationModel, idle_conversation) is not None
    assert await _contact_of(db_session, idle_conversation) is not None


@dataclass(frozen=True)
class _Merged:
    customer: UUID
    anonymous: UUID
    conversation_id: UUID
    visitor: dict
    key: str


async def _visitor_merged_into_a_customer(
    client: AsyncClient, db_session: AsyncSession, *, pod_id: str, owner: UUID
) -> _Merged:
    """A visitor who chatted and wrote a row as who they were then, and then
    proved the email of a contact the pod already had."""
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
    widget = await _widget(client, pod_id, name="Merge")
    key = widget["public_key"]
    email = f"returning-{widget['id'][:8]}@client.example"
    async with get_uow_factory()() as uow:
        customer = await open_contact(
            uow,
            pod_id=UUID(pod_id),
            kind=IdentityKind.EMAIL,
            value=email,
            strength=IdentityStrength.CHANNEL,
            display_name="Dana",
        )
        await uow.commit()

    visitor = await _session(client, key)
    conversation_id = await _say(
        client,
        db_session,
        key=key,
        visitor=visitor,
        text="Is my order shipped?",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("Let me look.")],
    )
    anonymous = await _contact_of(db_session, conversation_id)
    assert anonymous is not None and anonymous != customer.id
    # A row of theirs, as the visitor they were then.
    ordered = await client.post(
        f"/pods/{pod_id}/datastore/tables/orders/records",
        json={"data": {"item": "Blue kettle", "contact_id": str(anonymous)}},
    )
    assert ordered.status_code == 201, ordered.text

    sent = await client.post(
        f"/public/web/{key}/code", json={"email": email}, headers=_as(visitor)
    )
    assert sent.status_code == 200, sent.text
    verified = await client.post(
        f"/public/web/{key}/code/verify",
        json={"email": email, "code": _latest_code(email)},
        headers=_as(visitor),
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["is_contact"] is True
    assert verified.json()["display_name"] == "Dana"

    return _Merged(customer.id, anonymous, conversation_id, visitor, key)


@pytest.mark.drives_work
async def test_a_visitor_who_proves_an_older_contacts_email_becomes_that_contact(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """One person, one contact: the visitor's chat and rows follow them to the
    contact the pod already had, and the anonymous one is left pointing there
    -- until that person is forgotten, and goes with them."""
    pod_id = test_pod["id"]
    proved = await _visitor_merged_into_a_customer(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        owner=UUID(fixed_test_user["id"]),
    )
    customer_id, anonymous = proved.customer, proved.anonymous
    conversation_id, visitor = proved.conversation_id, proved.visitor
    db_session.expire_all()
    merged = await db_session.get(ContactModel, anonymous)
    assert merged is not None and merged.merged_into_id == customer_id
    session_contact = await db_session.scalar(
        select(VisitorSessionModel.contact_id).where(
            VisitorSessionModel.id == _session_id(visitor)
        )
    )
    assert session_contact == customer_id
    # The browser's handle moved with them; the old contact holds nothing.
    assert await _handles_of(db_session, customer_id) == {
        ("EMAIL", "CHANNEL"),
        ("WEB", "SESSION"),
    }
    assert await _handles_of(db_session, anonymous) == set()

    # The conversation and the row follow, by the work the merge wrote with it.
    registry = WorkRegistry()
    register_contact_merged_work(registry)
    register_datastore_contact_merged_work(registry)
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )
    assert await _contact_of(db_session, conversation_id) == customer_id
    rows = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/orders/records"
    )
    assert [(r["item"], r["contact_id"]) for r in rows.json()["items"]] == [
        ("Blue kettle", str(customer_id))
    ]

    # The pod lists one person, not two; the old id still leads to them.
    listed = await authenticated_client.get(f"/pods/{pod_id}/contacts")
    assert listed.status_code == 200, listed.text
    ids = {item["id"] for item in listed.json()["items"]}
    assert str(customer_id) in ids and str(anonymous) not in ids
    followed = await authenticated_client.get(f"/pods/{pod_id}/contacts/{anonymous}")
    assert followed.status_code == 200, followed.text
    assert followed.json()["id"] == str(customer_id)

    # Forgetting them forgets the row they wrote as a visitor, and the contact
    # they were then does not come back as an empty one.
    forgotten = await authenticated_client.delete(
        f"/pods/{pod_id}/contacts/{customer_id}"
    )
    assert forgotten.status_code in (200, 204), forgotten.text
    rows = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/orders/records"
    )
    assert rows.json()["items"] == []
    listed = await authenticated_client.get(f"/pods/{pod_id}/contacts")
    assert str(anonymous) not in {item["id"] for item in listed.json()["items"]}
    db_session.expire_all()
    assert await db_session.get(ContactModel, anonymous) is None


@pytest.mark.drives_work
async def test_forgetting_a_person_before_their_merge_is_carried_out_takes_it_all(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """The row and the chat move to the person in queued work after the merge.
    Forgotten before that work runs, they go anyway: under the visitor's old
    id, which forgetting the person reaches too."""
    pod_id = test_pod["id"]
    proved = await _visitor_merged_into_a_customer(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        owner=UUID(fixed_test_user["id"]),
    )

    forgotten = await authenticated_client.delete(
        f"/pods/{pod_id}/contacts/{proved.customer}"
    )

    assert forgotten.status_code in (200, 204), forgotten.text
    rows = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/orders/records"
    )
    assert rows.json()["items"] == []
    db_session.expire_all()
    assert await db_session.get(ConversationModel, proved.conversation_id) is None
    assert await db_session.get(ContactModel, proved.anonymous) is None
    # The work the merge queued then finds nothing to move, and fails nothing.
    registry = WorkRegistry()
    register_contact_merged_work(registry)
    register_datastore_contact_merged_work(registry)
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )
    rows = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/orders/records"
    )
    assert rows.json()["items"] == []


async def test_a_session_whose_conversation_names_nobody_is_given_its_own(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """A session's conversation is continued only while it answers the
    visitor. One that names nobody -- as a conversation from before contacts
    were a column does -- would answer them with the keeper's authority."""
    pod_id = test_pod["id"]
    owner = UUID(fixed_test_user["id"])
    key = (await _widget(authenticated_client, pod_id, name="Older"))["public_key"]
    visitor = await _session(authenticated_client, key)
    first = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="Hello?",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("Hi.")],
    )
    contact = await _contact_of(db_session, first)
    await db_session.execute(
        update(ConversationModel)
        .where(ConversationModel.id == first)
        .values(contact_id=None)
    )
    await db_session.commit()

    again = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="Still there?",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("Yes.")],
    )

    assert again != first
    assert await _contact_of(db_session, again) == contact


@pytest.mark.drives_work
async def test_a_visitor_writing_right_after_proving_who_they_are_keeps_their_chat(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    """Their conversation still names the contact they were until the merge's
    queued work moves it; it is theirs all the same, and is continued."""
    pod_id = test_pod["id"]
    owner = UUID(fixed_test_user["id"])
    proved = await _visitor_merged_into_a_customer(
        authenticated_client, db_session, pod_id=pod_id, owner=owner
    )
    again = await _say(
        authenticated_client,
        db_session,
        key=proved.key,
        visitor=proved.visitor,
        text="And the blue one?",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("Shipped too.")],
    )

    assert again == proved.conversation_id

    # Once the merge's work has run, the contact they were has no channel left;
    # the customer had no chat of their own, so this one takes its seat in
    # theirs rather than a new one being opened beside it.
    await _carry_out_merges()
    later = await _say(
        authenticated_client,
        db_session,
        key=proved.key,
        visitor=proved.visitor,
        text="And the red one?",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("Not yet.")],
    )
    assert later == proved.conversation_id
    db_session.expire_all()
    seated = await db_session.get(ConversationModel, later)
    assert seated is not None and seated.channel_id is not None


@pytest.mark.drives_work
async def test_two_browsers_that_prove_one_email_carry_on_in_one_conversation(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    monkeypatch,
):
    """Two browsers chat, then both prove one email: one contact, one channel,
    and one conversation in it. The second browser's conversation lost its seat
    to the first's when their channels became one; its next word continues the
    conversation that holds the seat, rather than claiming it a second time --
    and the agent answering it has read what was said in both."""
    pod_id = test_pod["id"]
    owner = UUID(fixed_test_user["id"])
    widget = await _widget(authenticated_client, pod_id, name="Two browsers")
    key = widget["public_key"]
    email = f"two-{widget['id'][:8]}@client.example"
    laptop = await _session(authenticated_client, key)
    phone = await _session(authenticated_client, key)
    on_laptop = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=laptop,
        text="Hi from my laptop",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("Hello.")],
    )
    on_phone = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=phone,
        text="Hi from my phone",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("Hello again.")],
    )
    assert on_phone != on_laptop
    for visitor in (laptop, phone):
        sent = await authenticated_client.post(
            f"/public/web/{key}/code", json={"email": email}, headers=_as(visitor)
        )
        assert sent.status_code == 200, sent.text
        verified = await authenticated_client.post(
            f"/public/web/{key}/code/verify",
            json={"email": email, "code": _latest_code(email)},
            headers=_as(visitor),
        )
        assert verified.status_code == 200, verified.text
    await _carry_out_merges()

    requests = record_model_requests(monkeypatch)
    again = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=phone,
        text="Still me",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("Yes.")],
    )

    assert again == on_laptop
    db_session.expire_all()
    seated = await db_session.get(ConversationModel, on_laptop)
    assert seated is not None and seated.channel_id is not None
    in_channel = await db_session.scalars(
        select(ConversationModel.id).where(
            ConversationModel.channel_id == seated.channel_id
        )
    )
    assert list(in_channel) == [on_laptop]
    # What the phone said before is still in its chat, and in the one the
    # agent answers from: the conversation that kept the seat holds it too.
    history = await authenticated_client.get(
        f"/public/web/{key}/history", headers=_as(phone)
    )
    assert history.status_code == 200, history.text
    texts = [message["text"] for message in history.json()["messages"]]
    for said in ("Hi from my laptop", "Hi from my phone", "Hello again.", "Still me"):
        assert said in texts, (said, texts)
    assert texts.count("Hi from my phone") == 1
    # The phone's words, and the agent's answer to them, were in no run of the
    # conversation that kept the seat: carried on purpose, once, and read as
    # the other chat's rather than as the newest thing said in this one.
    seen = requests[0]["text"]
    _read_in_order(
        seen,
        "Hi from my laptop",
        "Hello.",
        FOLDED_CHAT_OPENS,
        "Hi from my phone",
        "Hello again.",
        FOLDED_CHAT_ENDS,
        "Still me",
    )
    assert seen.count("Hi from my phone") == 1


async def _carry_out_merges() -> None:
    """Run the work each module queued when two contacts became one."""
    registry = WorkRegistry()
    register_contact_merged_work(registry)
    register_datastore_contact_merged_work(registry)
    register_channels_contact_merged_work(registry)
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )


@pytest.mark.drives_work
async def test_forgetting_a_person_reaches_every_contact_merged_into_them(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    monkeypatch,
):
    """However many contacts became this one, each one's rows go: they are
    read a page at a time, and none is left for the cascade to strand."""
    from app.modules.contacts.infrastructure import repository as contacts_repo
    from app.modules.contacts.services import forget as forget_service

    monkeypatch.setattr(contacts_repo, "ABSORBED_PAGE", 1)
    monkeypatch.setattr(forget_service, "ABSORBED_PAGE", 1)
    pod_id = test_pod["id"]
    created = await authenticated_client.post(
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
    async with get_uow_factory()() as uow:
        people = [
            await open_contact(
                uow,
                pod_id=UUID(pod_id),
                kind=IdentityKind.EMAIL,
                value=f"many-{n}@client.example",
                strength=IdentityStrength.CHANNEL,
                display_name=None,
            )
            for n in range(4)
        ]
        await uow.commit()
    survivor, absorbed = people[0], people[1:]
    for contact in absorbed:
        await db_session.execute(
            update(ContactModel)
            .where(ContactModel.id == contact.id)
            .values(merged_into_id=survivor.id)
        )
        ordered = await authenticated_client.post(
            f"/pods/{pod_id}/datastore/tables/orders/records",
            json={"data": {"item": "kettle", "contact_id": str(contact.id)}},
        )
        assert ordered.status_code == 201, ordered.text
    await db_session.commit()

    forgotten = await authenticated_client.delete(
        f"/pods/{pod_id}/contacts/{survivor.id}"
    )

    assert forgotten.status_code in (200, 204), forgotten.text
    rows = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/orders/records"
    )
    assert rows.json()["items"] == []


async def test_hosted_pages_live_on_their_own_origin_when_one_is_set(
    authenticated_client: AsyncClient, test_pod, monkeypatch
):
    widget = await _widget(authenticated_client, test_pod["id"], name="Pages")
    key = widget["public_key"]
    monkeypatch.setattr(
        public_web_settings, "public_pages_url", "https://pages.example"
    )

    on_the_api = await authenticated_client.get(f"/public/web/{key}/page")
    on_pages = await authenticated_client.get(
        f"/public/web/{key}/page", headers={"Host": "pages.example"}
    )
    assert on_the_api.status_code == 404
    assert on_pages.status_code == 200

    # The API's own origin is no longer a page every widget allows; the pages
    # origin is.
    from_api = await authenticated_client.post(
        f"/public/web/{key}/session",
        json={},
        headers={"Origin": "https://surface-e2e.test", "Authorization": ""},
    )
    from_pages = await authenticated_client.post(
        f"/public/web/{key}/session",
        json={},
        headers={"Origin": "https://pages.example", "Authorization": ""},
    )
    assert from_api.status_code == 403
    assert from_pages.status_code == 200, from_pages.text
    assert from_pages.headers["access-control-allow-origin"] == "https://pages.example"


async def test_a_hosted_page_keeps_its_visitors_chat_in_a_cookie_of_its_own(
    authenticated_client: AsyncClient, test_pod, monkeypatch
):
    """Safari clears what a script stores after a week; a first-party HttpOnly
    cookie set by Lemma's own pages host it keeps. A widget on somebody else's
    site gets no cookie: there it would be a third party's."""
    widget = await _widget(authenticated_client, test_pod["id"], name="Kept")
    key = widget["public_key"]
    monkeypatch.setattr(
        public_web_settings, "public_pages_url", "https://pages.example"
    )
    on_pages = {
        "Host": "pages.example",
        "Origin": "https://pages.example",
        "Authorization": "",
    }
    cookie = f"lemma_chat_{UUID(widget['id']).hex}"

    first = await authenticated_client.post(
        f"/public/web/{key}/session", json={}, headers=on_pages
    )
    assert first.status_code == 200, first.text
    set_cookie = first.headers["set-cookie"]
    assert set_cookie.startswith(f"{cookie}=")
    assert "HttpOnly" in set_cookie and "Secure" in set_cookie
    assert "Path=/public/web" in set_cookie
    # Sealed, never the secret itself.
    sealed = first.cookies.get(cookie) or set_cookie.split(";")[0].split("=", 1)[1]
    assert first.json()["secret"] not in set_cookie

    # The page lost its storage; the cookie brings the visitor back.
    back = await authenticated_client.post(
        f"/public/web/{key}/session",
        json={},
        headers={**on_pages, "Cookie": f"{cookie}={sealed}"},
    )
    assert back.status_code == 200, back.text
    assert _session_id(back.json()) == _session_id(first.json())

    # A cookie this deployment did not seal is as if there were none: the
    # bare secret, or anything else, starts a new chat.
    for forged in (first.json()["secret"], "not-a-live-secret"):
        fresh = await authenticated_client.post(
            f"/public/web/{key}/session",
            json={},
            headers={**on_pages, "Cookie": f"{cookie}={forged}"},
        )
        assert fresh.status_code == 200, fresh.text
        assert _session_id(fresh.json()) != _session_id(first.json())

    monkeypatch.setattr(public_web_settings, "public_pages_url", None)
    embedded = await authenticated_client.post(
        f"/public/web/{key}/session", json={}, headers=_page()
    )
    assert embedded.status_code == 200, embedded.text
    assert "set-cookie" not in embedded.headers


def _solved(challenge: dict) -> str:
    """What the widget's solver sends back: the number, found the slow way."""
    number = next(
        n
        for n in range(challenge["maxnumber"] + 1)
        if hashlib.sha256(f"{challenge['salt']}{n}".encode()).hexdigest()
        == challenge["challenge"]
    )
    answer = {**challenge, "number": number}
    del answer["enabled"], answer["maxnumber"]
    return base64.urlsafe_b64encode(json.dumps(answer).encode()).decode().rstrip("=")


async def test_with_bot_protection_on_a_new_chat_takes_a_proof_of_work(
    authenticated_client: AsyncClient, test_pod, monkeypatch
):
    """On by default, with nothing to configure: sign-in's switch stays off and
    no HMAC key is set, as on a fresh self-hosted deployment."""
    from app.modules.identity.config import identity_settings

    assert public_web_settings.model_fields["public_web_altcha_enabled"].default
    monkeypatch.setattr(public_web_settings, "public_web_altcha_enabled", True)
    monkeypatch.setattr(settings, "auth_altcha_enabled", False)
    monkeypatch.setattr(identity_settings, "auth_altcha_hmac_key", None)
    monkeypatch.setattr(identity_settings, "auth_altcha_max_number", 10_000)
    widget = await _widget(authenticated_client, test_pod["id"], name="Guarded")
    key = widget["public_key"]

    bare = await authenticated_client.post(
        f"/public/web/{key}/session", json={}, headers=_page()
    )
    assert bare.status_code == 400
    assert bare.json()["code"] == "altcha_failed"

    challenge = (
        await authenticated_client.get(f"/public/web/{key}/challenge", headers=_page())
    ).json()
    assert challenge["enabled"] is True
    proof = _solved(challenge)
    visitor = await _session(authenticated_client, key, altcha=proof)
    # A proof is good once, and a returning visitor needs none.
    again = await authenticated_client.post(
        f"/public/web/{key}/session", json={"altcha": proof}, headers=_page()
    )
    assert again.status_code == 400
    assert await _session(authenticated_client, key, secret=visitor["secret"])

    # Asking for a code takes one of its own: a session's proof does not do.
    session_proof = _solved(
        (await authenticated_client.get(f"/public/web/{key}/challenge")).json()
    )
    refused = await authenticated_client.post(
        f"/public/web/{key}/code",
        json={"email": "ana@client.example", "altcha": session_proof},
        headers=_as(visitor),
    )
    assert refused.status_code == 400


async def test_past_the_contacts_cap_a_visitor_is_told_a_person_will_reply(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    monkeypatch,
):
    """The cap holds the web chat as it holds every other door: no run starts,
    the message is kept for the member, and the visitor reads why, once. Once
    the cap is lifted, the agent answering them has read all of it."""
    pod_id = test_pod["id"]
    widget = await _widget(authenticated_client, pod_id)
    key = widget["public_key"]
    capped = await authenticated_client.put(
        f"/usage/organizations/{fixed_test_org['id']}/contacts-cap",
        json={"monthly_limit_usd": 0},
    )
    assert capped.status_code == 200, capped.text
    visitor = await _session(authenticated_client, key)

    for text in ("Is the shop open?", "Hello?"):
        with suppress_agent_run_enqueue():
            sent = await authenticated_client.post(
                f"/public/web/{key}/messages", json={"text": text}, headers=_as(visitor)
            )
        assert sent.status_code == 202, sent.text

    conversation_id = await _conversation_of(db_session, visitor)
    assert conversation_id is not None
    runs = (
        await db_session.scalars(
            select(AgentRunModel.id).where(
                AgentRunModel.conversation_id == conversation_id
            )
        )
    ).all()
    assert runs == []
    history = await authenticated_client.get(
        f"/public/web/{key}/history", headers=_as(visitor)
    )
    assert history.status_code == 200, history.text
    said = [m["text"] for m in history.json()["messages"]]
    assert "Is the shop open?" in said and "Hello?" in said
    assert sum("will reply here" in text for text in said) == 1

    lifted = await authenticated_client.put(
        f"/usage/organizations/{fixed_test_org['id']}/contacts-cap",
        json={"monthly_limit_usd": None},
    )
    assert lifted.status_code == 200, lifted.text
    requests = record_model_requests(monkeypatch)
    await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="Are you open now?",
        owner=UUID(fixed_test_user["id"]),
        pod_id=pod_id,
        script=[script_text("We are, until six.")],
    )
    seen = requests[0]["text"]
    _read_in_order(
        seen, "Is the shop open?", "will reply here", "Hello?", "Are you open now?"
    )
    assert seen.count("will reply here") == 1


def _read_in_order(seen: str, *lines: str) -> None:
    """Every line is in what the model was sent, in this order."""
    at, before = 0, ""
    for line in lines:
        found = seen.find(line, at)
        assert found >= 0, f"the model was never sent {line!r}{before}"
        at, before = found + len(line), f" after {line!r}"


@pytest.mark.drives_work
async def test_a_visitor_a_member_had_is_answered_with_all_they_said_meanwhile(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    monkeypatch,
):
    """While a member has the chat no run answers the visitor, and their
    messages wait for the member. Once the agent has them back, it reads those
    messages before the one it answers -- not the newest alone, as if there
    were no earlier thread."""
    pod_id = test_pod["id"]
    owner = UUID(fixed_test_user["id"])
    widget = await _widget(authenticated_client, pod_id, name="Held for a member")
    key = widget["public_key"]
    visitor = await _session(authenticated_client, key)
    conversation_id = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="Is the blue one in stock?",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("Let me check.")],
    )
    contact = await _contact_of(db_session, conversation_id)
    channel = await contact_channel_id(authenticated_client, pod_id, str(contact))
    # The member steps in by writing to them.
    await member_writes(authenticated_client, pod_id, channel, "We have two left.")
    await send_deliveries()

    held = ("Can you keep one for me?", "I can pick it up on Friday.")
    for text in held:
        with suppress_agent_run_enqueue():
            sent = await authenticated_client.post(
                f"/public/web/{key}/messages", json={"text": text}, headers=_as(visitor)
            )
        assert sent.status_code == 202, sent.text
    runs = await db_session.scalars(
        select(AgentRunModel.id).where(AgentRunModel.conversation_id == conversation_id)
    )
    assert len(runs.all()) == 1, "a run answered while the member had the chat"

    handed_back = await authenticated_client.patch(
        f"/pods/{pod_id}/channels/{channel}", json={"agents_answer": True}
    )
    assert handed_back.status_code == 200, handed_back.text
    requests = record_model_requests(monkeypatch)
    await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="Hello? Is that a yes?",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("Yes, one is kept for you until Friday.")],
    )

    _read_in_order(
        requests[0]["text"],
        "Is the blue one in stock?",
        "Let me check.",
        "We have two left.",
        *held,
        "Hello? Is that a yes?",
    )


async def test_past_the_contacts_cap_a_visitors_message_is_held_without_a_ledger_sum(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_org,
):
    """Every message asks the cap, and reads it from the running total the
    visitor's run would be admitted on: once the month is counted, being held
    costs no sum over the month's spend."""
    from app.modules.agent.infrastructure.models import AgentRunModel

    widget = await _widget(authenticated_client, test_pod["id"])
    key = widget["public_key"]
    capped = await authenticated_client.put(
        f"/usage/organizations/{fixed_test_org['id']}/contacts-cap",
        json={"monthly_limit_usd": 0},
    )
    assert capped.status_code == 200, capped.text
    visitor = await _session(authenticated_client, key)

    async def send(text: str) -> None:
        with suppress_agent_run_enqueue():
            sent = await authenticated_client.post(
                f"/public/web/{key}/messages", json={"text": text}, headers=_as(visitor)
            )
        assert sent.status_code == 202, sent.text

    # The first asks a new window, and counts it from the ledger.
    await send("Is the shop open?")
    with counted_queries() as held:
        await send("Hello?")

    sums = [
        statement
        for statement in held
        if "sum(" in statement.lower()
        and ("usage_records" in statement or "usage_limit_counters" in statement)
    ]
    assert not sums, format_statements(sums)
    assert statements_touching(held, "usage_limit_counters"), format_statements(held)
    conversation_id = await _conversation_of(db_session, visitor)
    runs = await db_session.scalars(
        select(AgentRunModel.id).where(AgentRunModel.conversation_id == conversation_id)
    )
    assert runs.all() == []
