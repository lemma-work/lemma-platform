"""A web widget, end to end: a public key, a visitor, and the pod's answer.

The whole path with only the model's next move scripted. A member puts a chat
widget on a shop's site; an anonymous visitor writes and is answered from what
the pod made Public; a signed-in customer of the shop, named by a token the
shop's server signed, is answered as a contact; an anonymous visitor who proves
an email address becomes one, keeping the conversation. And the refusals: a
page on another site, a forged or long-lived token, a known-only widget's
strangers, a form that needs a code.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from uuid import UUID

import jwt
import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.modules.agent.domain.outsiders import (
    AUDIENCE_KEY,
    CONTACT,
    CONTACT_KEY,
    OUTSIDERS,
)
from app.modules.agent.infrastructure.models import ConversationModel
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent_surfaces.infrastructure.web_widget_models import (
    WebSessionModel,
)
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
from app.modules.test_support.e2e.scripted_model import script_thinking

pytestmark = pytest.mark.e2e

SHOP = "https://shop.example"


def _text(body: dict) -> dict:
    """How the widget sends a request: JSON as text/plain, so no pre-flight."""
    return {
        "content": json.dumps(body),
        "headers": {"Content-Type": "text/plain;charset=UTF-8", "Origin": SHOP},
    }


async def _widget(client: AsyncClient, pod_id: str, **overrides) -> dict:
    created = await client.post(
        f"/pods/{pod_id}/web-widgets",
        json={"name": "Shop chat", "allowed_origins": [SHOP], **overrides},
    )
    assert created.status_code == 201, created.text
    return created.json()


async def _session(client: AsyncClient, key: str, **body) -> dict:
    started = await client.post(f"/public/web/{key}/session", **_text(body))
    assert started.status_code == 200, started.text
    return started.json()


async def _say(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    key: str,
    session: str,
    text: str,
    owner: UUID,
    pod_id: str,
    script: list,
) -> UUID:
    with suppress_agent_run_enqueue():
        sent = await client.post(
            f"/public/web/{key}/messages", **_text({"session": session, "text": text})
        )
    assert sent.status_code == 202, sent.text
    db_session.expire_all()
    conversation_id = (
        (
            await db_session.execute(
                select(WebSessionModel.conversation_id)
                .where(WebSessionModel.conversation_id.is_not(None))
                .order_by(WebSessionModel.updated_at.desc())
            )
        )
        .scalars()
        .first()
    )
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
        content="{}",
        headers={"Content-Type": "text/plain", "Origin": "https://evil.example"},
    )
    assert elsewhere.status_code == 403
    assert "access-control-allow-origin" not in elsewhere.headers

    started = await authenticated_client.post(f"/public/web/{key}/session", **_text({}))
    assert started.status_code == 200, started.text
    assert started.headers["access-control-allow-origin"] == SHOP
    session = started.json()
    assert session["is_contact"] is False

    conversation_id = await _say(
        authenticated_client,
        db_session,
        key=key,
        session=session["session"],
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

    history = await authenticated_client.post(
        f"/public/web/{key}/history", **_text({"session": session["session"]})
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

    # Somebody else's session token reads nothing.
    stranger = await authenticated_client.post(
        f"/public/web/{key}/history", **_text({"session": "not-a-session"})
    )
    assert stranger.status_code == 401


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
    ):
        refused = await authenticated_client.post(
            f"/public/web/{key}/session", **_text({"host_token": bad})
        )
        assert refused.status_code == 401, refused.text

    session = await _session(
        authenticated_client,
        key,
        host_token=_host_token(secret, key, subject="cust-42"),
    )
    assert session["is_contact"] is True
    assert session["display_name"] == "Ana"

    conversation_id = await _say(
        authenticated_client,
        db_session,
        key=key,
        session=session["session"],
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
    assert identity.value == f"{widget['id']}:cust-42"
    assert identity.strength == "HOST"
    assert str(identity.contact_id) == metadata.get(CONTACT_KEY)

    # Rotating the secret retires every token signed with the old one.
    rotated = await authenticated_client.post(
        f"/pods/{pod_id}/web-widgets/{widget['id']}/secret"
    )
    assert rotated.status_code == 200, rotated.text
    stale = await authenticated_client.post(
        f"/public/web/{key}/session",
        **_text({"host_token": _host_token(secret, key, subject="cust-42")}),
    )
    assert stale.status_code == 401


def _latest_code(email: str) -> str:
    spool = sorted(
        Path(settings.email_output_dir).glob(f"*{email.replace('@', '_at_')}*")
    )
    assert spool, "no code was sent"
    body = json.loads(spool[-1].read_text())
    match = re.search(r"\b(\d{6})\b", json.dumps(body))
    assert match is not None
    return match.group(1)


async def test_a_visitor_who_proves_an_email_becomes_a_contact_in_the_same_chat(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    widget = await _widget(authenticated_client, pod_id)
    key = widget["public_key"]
    session = await _session(authenticated_client, key)
    conversation_id = await _say(
        authenticated_client,
        db_session,
        key=key,
        session=session["session"],
        text="Hi",
        owner=UUID(fixed_test_user["id"]),
        pod_id=pod_id,
        script=[script_text("Hello!")],
    )
    assert (await _metadata(db_session, conversation_id)).get(AUDIENCE_KEY) == OUTSIDERS

    email = f"visitor-{widget['id'][:8]}@client.example"
    sent = await authenticated_client.post(
        f"/public/web/{key}/code",
        **_text({"session": session["session"], "email": email}),
    )
    assert sent.status_code == 200, sent.text
    wrong = await authenticated_client.post(
        f"/public/web/{key}/code/verify",
        **_text({"session": session["session"], "email": email, "code": "000000x"}),
    )
    assert wrong.status_code == 400
    verified = await authenticated_client.post(
        f"/public/web/{key}/code/verify",
        **_text(
            {"session": session["session"], "email": email, "code": _latest_code(email)}
        ),
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["is_contact"] is True

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


async def test_a_known_only_widget_and_a_coded_form_refuse_strangers(
    authenticated_client: AsyncClient, test_pod
):
    pod_id = test_pod["id"]
    known = await _widget(authenticated_client, pod_id, name="Members", answer="known")
    session = await _session(authenticated_client, known["public_key"])
    refused = await authenticated_client.post(
        f"/public/web/{known['public_key']}/messages",
        **_text({"session": session["session"], "text": "Hello?"}),
    )
    assert refused.status_code == 403

    form = await _widget(
        authenticated_client,
        pod_id,
        name="Support form",
        kind="form",
        form_function="create_ticket",
        form_requires_code=True,
    )
    anonymous = await authenticated_client.post(
        f"/public/web/{form['public_key']}/submit",
        **_text({"input": {"subject": "Broken"}}),
    )
    assert anonymous.status_code == 403

    open_form = await _widget(
        authenticated_client,
        pod_id,
        name="Feedback",
        kind="form",
        form_function="not_opened_to_contacts",
    )
    unavailable = await authenticated_client.post(
        f"/public/web/{open_form['public_key']}/submit",
        **_text({"input": {"text": "Nice"}}),
    )
    assert unavailable.status_code == 404

    off = await authenticated_client.patch(
        f"/pods/{pod_id}/web-widgets/{known['id']}", json={"answer": "off"}
    )
    assert off.status_code == 200, off.text
    gone = await authenticated_client.post(
        f"/public/web/{known['public_key']}/session", **_text({})
    )
    assert gone.status_code == 404


async def test_the_widget_script_is_served_without_a_session(async_client: AsyncClient):
    script = await async_client.get("/public/web/widget.js")
    assert script.status_code == 200
    assert script.headers["content-type"].startswith("text/javascript")
    assert "data-lemma-key" in script.text


async def test_a_follow_up_waits_in_a_web_contacts_chat(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
):
    pod_id = test_pod["id"]
    widget = await _widget(authenticated_client, pod_id, name="Follow-ups")
    key, secret = widget["public_key"], widget["signing_secret"]
    session = await _session(
        authenticated_client, key, host_token=_host_token(secret, key, subject="cust-7")
    )
    await _say(
        authenticated_client,
        db_session,
        key=key,
        session=session["session"],
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
    history = await authenticated_client.post(
        f"/public/web/{key}/history", **_text({"session": session["session"]})
    )
    assert ("assistant", "It's back in stock.") in [
        (m["role"], m["text"]) for m in history.json()["messages"]
    ]
