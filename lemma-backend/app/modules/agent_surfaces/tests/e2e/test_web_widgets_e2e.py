"""A web widget, end to end: a public key, a visitor, and the pod's answer.

The whole path with only the model's next move scripted. A member puts a chat
widget on a shop's site; an anonymous visitor writes and is answered from what
the pod made Public; a signed-in customer of the shop, named by a token the
shop's server signed, is answered as a contact; an anonymous visitor who proves
an email address becomes one, keeping the conversation. And the refusals: a
page on another site, a forged or long-lived token, a host session that tries
to outlive its token, a known-only widget's strangers, every guess at a code
counted however fast they come, and a deployment with public web switched off.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.api.dependencies import get_uow_factory
from app.core.config import settings
from app.core.public_web import public_web_settings
from app.modules.agent.domain.outsiders import (
    AUDIENCE_KEY,
    CONTACT,
    CONTACT_KEY,
    OUTSIDERS,
)
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent_surfaces.events.visitor_retention import sweep_web_visitors
from app.modules.agent_surfaces.tests.e2e.helpers import _messages_for_conversation
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    run_scripted_agent_run,
    script_text,
    script_tool_call,
)
from app.modules.agent_surfaces.tests.e2e.test_telegram_group_outsiders_e2e import (
    _table,
)
from app.modules.contacts.infrastructure.models import ContactIdentityModel
from app.modules.contacts.infrastructure.visitor_sessions import (
    VisitorCodeModel,
    VisitorSessionModel,
)
from app.modules.test_support.e2e.scripted_model import script_thinking

pytestmark = pytest.mark.e2e

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
) -> UUID:
    with suppress_agent_run_enqueue():
        sent = await client.post(
            f"/public/web/{key}/messages", json={"text": text}, headers=_as(visitor)
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


async def _metadata(db_session: AsyncSession, conversation_id: UUID) -> dict:
    db_session.expire_all()
    conversation = await db_session.get(ConversationModel, conversation_id)
    assert conversation is not None
    return conversation.conversation_metadata or {}


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
    assert (await _metadata(db_session, conversation_id)).get(AUDIENCE_KEY) == OUTSIDERS

    history = await authenticated_client.get(
        f"/public/web/{key}/history", headers=_as(visitor)
    )
    assert history.status_code == 200, history.text
    said = [(m["role"], m["text"]) for m in history.json()["messages"]]
    assert said == [
        ("user", "What do you sell?"),
        ("assistant", "Here is our price list."),
    ]

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
    metadata = await _metadata(db_session, conversation_id)
    assert metadata.get(AUDIENCE_KEY) == CONTACT
    identity = (
        await db_session.execute(
            select(ContactIdentityModel).where(
                ContactIdentityModel.pod_id == UUID(pod_id),
                ContactIdentityModel.kind == "HOST",
            )
        )
    ).scalar_one()
    # Keyed by the pod, so another of its widgets meets the same contact.
    assert identity.value == "host:cust-42"
    assert identity.strength == "HOST"
    assert str(identity.contact_id) == metadata.get(CONTACT_KEY)

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
    assert (await _metadata(db_session, conversation_id)).get(AUDIENCE_KEY) == OUTSIDERS

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

    # The conversation they already had is now theirs, as a contact.
    metadata = await _metadata(db_session, conversation_id)
    assert metadata.get(AUDIENCE_KEY) == CONTACT
    identity = (
        await db_session.execute(
            select(ContactIdentityModel).where(ContactIdentityModel.value == email)
        )
    ).scalar_one()
    assert identity.strength == "CODE"
    assert str(identity.contact_id) == metadata.get(CONTACT_KEY)

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


async def test_a_follow_up_waits_in_a_web_contacts_chat(
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

    sent = await authenticated_client.post(
        f"/pods/{pod_id}/contacts/{contact}/messages",
        json={"message": "It's back in stock."},
    )
    assert sent.status_code == 200, sent.text
    assert sent.json() | {"conversation_id": None} == {
        "conversation_id": None,
        "platform": "WEB",
        "delivered": False,
    }
    history = await authenticated_client.get(
        f"/public/web/{key}/history", headers=_as(visitor)
    )
    assert ("assistant", "It's back in stock.") in [
        (m["role"], m["text"]) for m in history.json()["messages"]
    ]

    # Forgetting the contact ends the session that named them.
    forgotten = await authenticated_client.delete(f"/pods/{pod_id}/contacts/{contact}")
    assert forgotten.status_code == 204, forgotten.text
    after = await authenticated_client.get(
        f"/public/web/{key}/history", headers=_as(visitor)
    )
    assert after.status_code == 401


async def test_the_sweep_clears_what_visitors_left_behind(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    widget = await _widget(authenticated_client, pod_id, name="Sweep")
    key = widget["public_key"]
    gone_by = await _session(authenticated_client, key)
    idle = await _session(authenticated_client, key)
    kept = await _session(authenticated_client, key)
    idle_conversation = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=idle,
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

    codes, sessions, conversations = await sweep_web_visitors(get_uow_factory())

    assert sessions >= 1 and conversations >= 1 and codes >= 0
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
    # The ended session went; the idle conversation took its session with it.
    assert remaining == {_session_id(kept)}
    assert await db_session.get(ConversationModel, idle_conversation) is None


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
    from pydantic import SecretStr

    from app.modules.identity.config import identity_settings

    monkeypatch.setattr(settings, "auth_altcha_enabled", True)
    monkeypatch.setattr(identity_settings, "auth_altcha_hmac_key", SecretStr("k" * 32))
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
