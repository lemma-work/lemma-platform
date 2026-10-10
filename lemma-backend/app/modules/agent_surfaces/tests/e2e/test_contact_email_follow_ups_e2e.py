"""A follow-up by email to a web chat contact, end to end.

A visitor proves an email address in the shop's chat and leaves. A member asks
for the follow-up to go by email: it is sent from the pod's own address with
the unsubscribe line, in a new conversation that is the contact's, and the
visitor's reply comes back to that conversation as the same contact. Refused
when the address was unsubscribed, or when the pod has no email address
answering contacts; a contact with no verified address is refused email and
still has the message wait in their chat.
"""

from __future__ import annotations

import re
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.outsiders import AUDIENCE_KEY, CONTACT, CONTACT_KEY
from app.modules.agent.infrastructure.models import ConversationModel, MessageModel
from app.modules.agent_surfaces.domain.groups import contact_link_user
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
)
from app.modules.agent_surfaces.infrastructure.models import (
    AgentSurfaceConversationLinkModel,
)
from app.modules.agent_surfaces.services.contact_follow_ups import unsubscribe_token
from app.modules.agent_surfaces.tests.e2e.helpers import _resend_payload
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)
from app.modules.agent_surfaces.tests.e2e.test_email_contacts_e2e import _email_bot
from app.modules.agent_surfaces.tests.e2e.test_web_widgets_e2e import (
    _as,
    _host_token,
    _latest_code,
    _metadata,
    _say,
    _session,
    _widget,
)
from app.modules.contacts.infrastructure.models import ContactIdentityModel

pytestmark = pytest.mark.e2e


async def _web_contact_with_email(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    pod_id: str,
    owner: UUID,
    email: str,
) -> tuple[str, dict, UUID, UUID]:
    """A visitor who chatted, then proved ``email`` with a code.

    The widget key, the visitor, their chat's conversation, and the contact.
    """
    widget = await _widget(client, pod_id)
    key = widget["public_key"]
    visitor = await _session(client, key)
    chat = await _say(
        client,
        db_session,
        key=key,
        visitor=visitor,
        text="Is the blue one back in stock?",
        owner=owner,
        pod_id=pod_id,
        script=[script_text("I'll find out and let you know.")],
    )
    asked = await client.post(
        f"/public/web/{key}/code", json={"email": email}, headers=_as(visitor)
    )
    assert asked.status_code == 200, asked.text
    verified = await client.post(
        f"/public/web/{key}/code/verify",
        json={"email": email, "code": _latest_code(email)},
        headers=_as(visitor),
    )
    assert verified.status_code == 200, verified.text
    contact = UUID((await _metadata(db_session, chat))[CONTACT_KEY])
    return key, verified.json(), chat, contact


async def _follow_up(
    client: AsyncClient, pod_id: str, contact: UUID, message: str, **body
):
    return await client.post(
        f"/pods/{pod_id}/contacts/{contact}/messages",
        json={"message": message, **body},
    )


async def _chat_texts(client: AsyncClient, key: str, visitor: dict) -> list[str]:
    history = await client.get(f"/public/web/{key}/history", headers=_as(visitor))
    assert history.status_code == 200, history.text
    return [message["text"] for message in history.json()["messages"]]


async def test_a_web_chat_contact_is_followed_up_by_email_and_their_reply_returns(
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
    email = f"ana-{pod_id[:8]}@client.example"
    key, visitor, chat, contact = await _web_contact_with_email(
        authenticated_client, db_session, pod_id=pod_id, owner=owner, email=email
    )

    # No email address of the pod's answers contacts yet: nothing to send from.
    nowhere = await _follow_up(
        authenticated_client, pod_id, contact, "It's back.", channel="email"
    )
    assert nowhere.status_code == 409, nowhere.text
    assert nowhere.json()["code"] == "no_email_surface"

    _surface, address = await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="known",
    )
    message_store.clear()
    sent = await _follow_up(
        authenticated_client,
        pod_id,
        contact,
        "The blue one is back in stock.\n\nReply and I'll hold one for you.",
        channel="email",
    )
    assert sent.status_code == 200, sent.text
    body = sent.json()
    assert body["platform"] == "RESEND" and body["delivered"] is True
    thread = UUID(body["conversation_id"])
    assert thread != chat

    # Emailed from the pod's address, with the line and the link that stop it.
    [mail] = message_store.get_all("RESEND")
    assert mail["to"] == [email]
    assert address in mail["from"]
    assert mail["subject"] == "The blue one is back in stock."
    assert "Reply and I'll hold one for you." in mail["text"]
    assert "To stop these emails:" in mail["text"]
    link = re.search(r"/public/contacts/unsubscribe\?token=(\S+)", mail["text"])
    assert link is not None

    # A conversation of the contact's, looked after by the member, holding the
    # follow-up as sent; the chat on the shop's page is left as it was.
    metadata = await _metadata(db_session, thread)
    assert metadata[AUDIENCE_KEY] == CONTACT
    assert metadata[CONTACT_KEY] == str(contact)
    assert (await db_session.get(ConversationModel, thread)).user_id == owner
    [written] = (
        await db_session.scalars(
            select(MessageModel).where(MessageModel.conversation_id == thread)
        )
    ).all()
    assert written.message_metadata["follow_up"] is True
    assert written.message_metadata["delivered"] is True
    assert not any(
        "is back in stock." in text
        for text in await _chat_texts(authenticated_client, key, visitor)
    )
    seed = (
        await db_session.execute(
            select(AgentSurfaceConversationLinkModel.external_thread_id).where(
                AgentSurfaceConversationLinkModel.conversation_id == thread,
                AgentSurfaceConversationLinkModel.external_user_id
                == contact_link_user(contact),
            )
        )
    ).scalar_one()

    # Their reply threads under the seed, and lands there, as the same contact.
    reply = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=email,
                assistant_address=address,
                message_id="ana-reply-1",
                text="Yes please, hold one.",
                subject="Re: The blue one is back in stock.",
                in_reply_to="<sent-1@resend-e2e.test>",
                references=[seed, "<sent-1@resend-e2e.test>"],
            ),
            headers={},
        ),
        script=[script_text("Held one for you.")],
    )
    assert isinstance(reply, SurfaceChatContext)
    assert reply.conversation_id == thread
    assert reply.audience.contact_id == contact

    # A later follow-up with no channel goes on that thread, the latest.
    again = await _follow_up(authenticated_client, pod_id, contact, "Collected?")
    assert again.status_code == 200, again.text
    assert again.json()["conversation_id"] == str(thread)


async def test_an_unsubscribed_address_is_not_followed_up_by_email(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    pod_id = test_pod["id"]
    email = f"ben-{pod_id[:8]}@client.example"
    _key, _visitor, _chat, contact = await _web_contact_with_email(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        owner=UUID(fixed_test_user["id"]),
        email=email,
    )
    await _email_bot(
        authenticated_client,
        db_session,
        pod_id=pod_id,
        user=fixed_test_user,
        fake_resend=fake_resend,
        monkeypatch=monkeypatch,
        answer="known",
    )
    handle = (
        await db_session.execute(
            select(ContactIdentityModel.id).where(
                ContactIdentityModel.contact_id == contact,
                ContactIdentityModel.value == email,
            )
        )
    ).scalar_one()
    stopped = await authenticated_client.post(
        "/public/contacts/unsubscribe",
        data={"token": unsubscribe_token(handle)},
    )
    assert stopped.status_code == 200, stopped.text

    message_store.clear()
    refused = await _follow_up(
        authenticated_client, pod_id, contact, "One more thing.", channel="email"
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "unsubscribed"
    assert message_store.get_all("RESEND") == []


async def test_a_contact_with_no_verified_email_still_waits_in_the_web_chat(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
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
        answer="known",
    )
    # A customer the shop signed in: a contact by the shop's word, no address.
    widget = await _widget(authenticated_client, pod_id)
    key, secret = widget["public_key"], widget["signing_secret"]
    visitor = await _session(
        authenticated_client, key, host_token=_host_token(secret, key, subject="c-9")
    )
    chat = await _say(
        authenticated_client,
        db_session,
        key=key,
        visitor=visitor,
        text="Is the blue one back?",
        owner=UUID(fixed_test_user["id"]),
        pod_id=pod_id,
        script=[script_text("I'll check.")],
    )
    contact = UUID((await _metadata(db_session, chat))[CONTACT_KEY])

    message_store.clear()
    refused = await _follow_up(
        authenticated_client, pod_id, contact, "It's back.", channel="email"
    )
    assert refused.status_code == 409, refused.text
    assert refused.json()["code"] == "no_verified_email"

    waits = await _follow_up(authenticated_client, pod_id, contact, "It's back.")
    assert waits.status_code == 200, waits.text
    assert waits.json() == {
        "conversation_id": str(chat),
        "platform": "WEB",
        "delivered": False,
    }
    assert "It's back." in await _chat_texts(authenticated_client, key, visitor)
    assert message_store.get_all("RESEND") == []
