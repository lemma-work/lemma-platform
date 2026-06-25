from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.ingress_context import (
    SurfaceChatContext,
    SurfaceReplyContext,
)
from app.modules.agent_surfaces.domain.ingress_request import SurfacePlatformWebhookIngress
from app.modules.agent_surfaces.tests.e2e.helpers import (
    EmulatedAgentHarness,
    _conversation_by_external_thread,
    _create_surface,
    _process_ingress_and_emulate_reply,
    _seed_external_user,
    _set_user_mobile_number,
    _telegram_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_telegram_secret_headers,
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_harnesses import RecordPromptHarness

pytestmark = pytest.mark.e2e


async def test_telegram_built_in_dm_surface_uses_default_agent_and_replies(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):

    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "telegram_webhook_secret", "native-secret")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    pod_id = test_pod["id"]
    surface = await _create_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM"},
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="111222333",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    payload = _telegram_payload(
        text="Help me set up this pod",
        message_id=44,
        sender_id=111222333,
    )
    raw_body = json.dumps(payload).encode("utf-8")
    response = await authenticated_client.post(
        "/surfaces/webhooks/telegram",
        content=raw_body,
        headers=build_telegram_secret_headers("native-secret"),
    )
    assert response.status_code == 200, response.text

    harness = EmulatedAgentHarness()
    context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        harness,
    )
    assert isinstance(context, SurfaceChatContext)
    assert context.agent_name is None
    assert context.surface_id == UUID(surface["id"])

    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        external_thread_id="111222333",
    )
    assert conversation is not None
    assert conversation["agent_id"] is None

    telegram_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert telegram_messages[-1]["chat_id"] == "111222333"
    # Text is rendered as MarkdownV2, so reserved chars ([ ] _) are escaped.
    assert "E2E agent reply" in telegram_messages[-1]["text"]
    assert "TELEGRAM" in telegram_messages[-1]["text"]


def _telegram_group_payload(
    *,
    text: str,
    message_id: int,
    sender_id: int,
    chat_id: int,
    username: str = "surfaceuser",
    mention: bool = True,
    reply_to_bot: bool = False,
    reply_text: str = "",
) -> dict:
    message: dict = {
        "message_id": message_id,
        "from": {
            "id": sender_id,
            "is_bot": False,
            "first_name": "Surface",
            "username": username,
        },
        "chat": {"id": chat_id, "type": "supergroup", "title": "Lemma Test Group"},
        "date": 1700000000,
        "text": text,
    }
    if mention:
        # An @bot mention produces a "mention" entity, which is what Telegram
        # delivers to a privacy-mode bot in a group.
        message["entities"] = [{"type": "mention", "offset": 0, "length": 9}]
    if reply_to_bot:
        # Replying to the bot's own message continues the thread without a mention.
        message["reply_to_message"] = {
            "message_id": message_id - 1,
            "from": {"id": 12345, "is_bot": True, "username": "lemmabot"},
            "text": reply_text or "",
        }
    return {"update_id": message_id + 100000, "message": message}


def _wire_native_telegram(monkeypatch, fake_telegram) -> None:
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )


async def test_telegram_group_mention_routes_and_replies(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """The bot, @mentioned by a pod member in a group, routes to the surface's
    default agent and replies in the group chat."""
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    # The mentioning member is a pod user, resolved by their telegram username.
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="+15559990001",
        telegram_username="surfaceuser",
    )

    payload = _telegram_group_payload(
        text="@lemmabot summarize the thread",
        message_id=71,
        sender_id=900100,
        chat_id=-1001234567890,
    )
    harness = EmulatedAgentHarness()
    context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        harness,
    )
    assert isinstance(context, SurfaceChatContext)

    telegram_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    # Reply went back to the GROUP chat.
    assert telegram_messages[-1]["chat_id"] == "-1001234567890"
    assert "E2E agent reply" in telegram_messages[-1]["text"]


async def test_telegram_group_reply_to_bot_continues_without_mention(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """Replying to the bot's own message in a group continues the conversation
    without re-@mentioning it."""
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="+15559990003",
        telegram_username="surfaceuser",
    )

    payload = _telegram_group_payload(
        text="and what about next week?",
        message_id=74,
        sender_id=900100,
        chat_id=-1009876543210,
        mention=False,
        reply_to_bot=True,
    )
    harness = EmulatedAgentHarness()
    context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        harness,
    )
    assert isinstance(context, SurfaceChatContext)
    telegram_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert telegram_messages[-1]["chat_id"] == "-1009876543210"


async def test_telegram_group_injects_reply_as_channel_context(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """In a group, the message the user replied to is handed to the agent as
    background channel context (Telegram bots can't read full history)."""
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="+15559990004",
        telegram_username="surfaceuser",
    )

    payload = _telegram_group_payload(
        text="@lemmabot follow up on that",
        message_id=76,
        sender_id=900100,
        chat_id=-1005555555555,
        reply_to_bot=True,
        reply_text="Earlier the team agreed to ship on Friday.",
    )
    harness = RecordPromptHarness()
    context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        harness,
    )
    assert isinstance(context, SurfaceChatContext)
    assert harness.metadatas, "agent run did not record message metadata"
    channel_context = harness.metadatas[-1].get("channel_context")
    assert channel_context, channel_context
    assert any(
        "ship on Friday" in (m.get("text") or "") for m in channel_context
    ), channel_context


async def test_telegram_dm_has_no_channel_context(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """A 1:1 DM never fetches channel context (single user, no group)."""
    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id="222333555",
        resolved_user_id=UUID(fixed_test_user["id"]),
    )
    harness = RecordPromptHarness()
    context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_telegram_payload(text="hi", message_id=78, sender_id=222333555),
            headers={},
        ),
        harness,
    )
    assert isinstance(context, SurfaceChatContext)
    assert harness.metadatas
    assert "channel_context" not in harness.metadatas[-1]


async def test_telegram_group_without_mention_is_ignored(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """A plain group message that does NOT mention the bot is ignored (no reply),
    so the bot only speaks when addressed."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.events.handlers import (
        build_surface_event_handler,
    )

    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="+15559990002",
        telegram_username="surfaceuser",
    )

    payload = _telegram_group_payload(
        text="just chatting, no bot here",
        message_id=72,
        sender_id=900100,
        chat_id=-1001234567890,
        mention=False,
    )
    handler = build_surface_event_handler(SqlAlchemyUnitOfWork(db_session))
    context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={})
    )
    assert context is None
    assert message_store.get_all("TELEGRAM") == []


async def test_telegram_group_mention_of_other_user_is_ignored(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """A group message that @mentions ANOTHER user (not the bot) must not wake
    the bot. The `mention` entity is just a plain @username and doesn't identify
    the mentioned user, so the parser records it for bot-identity verification
    and the ingress enrichment confirms it is not the bot before ignoring."""
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.events.handlers import (
        build_surface_event_handler,
    )

    _wire_native_telegram(monkeypatch, fake_telegram)
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="+15559990005",
        telegram_username="surfaceuser",
    )

    payload = _telegram_group_payload(
        text="@otherperson can you check this?",
        message_id=80,
        sender_id=900100,
        chat_id=-1007777777777,
        mention=True,
    )
    # Override the default mention entity to point at "@otherperson" (13 chars)
    # so the parser extracts "otherperson" rather than the default "@lemmabot".
    payload["message"]["entities"] = [
        {"type": "mention", "offset": 0, "length": 13},
    ]
    handler = build_surface_event_handler(SqlAlchemyUnitOfWork(db_session))
    context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={})
    )
    assert context is None
    assert message_store.get_all("TELEGRAM") == []


async def test_telegram_username_resolves_user_without_contact_share(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """A Telegram sender whose @username matches a user's ``telegram_username``
    resolves directly — no contact-share (phone) flow needed."""
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    # The user has a telegram_username set (and an unrelated mobile number); the
    # external sender is NOT pre-linked and shares no contact.
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="+15559990000",
        telegram_username="surfaceuser",
    )

    # _telegram_payload's sender carries username="surfaceuser".
    payload = _telegram_payload(text="hello directly", message_id=70, sender_id=55501234)
    harness = EmulatedAgentHarness()
    context = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        harness,
    )
    # Resolved by username → a real chat (not the "share your contact" reply).
    assert isinstance(context, SurfaceChatContext)
    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        external_thread_id="55501234",
    )
    assert conversation is not None
    telegram_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert "E2E agent reply" in telegram_messages[-1]["text"]


async def test_telegram_contact_share_links_user_then_allows_chat(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):

    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="+15551234567",
        telegram_username="surfaceuser",
    )
    pod_id = test_pod["id"]
    await _create_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})

    contact_payload = {
        "update_id": 100500,
        "message": {
            "message_id": 501,
            "from": {"id": 123123123, "is_bot": False, "first_name": "Linked"},
            "chat": {"id": 123123123, "type": "private"},
            "date": 1700000100,
            "contact": {
                "phone_number": "+1 555 123 4567",
                "first_name": "Linked",
                "user_id": 123123123,
            },
        },
    }

    harness = EmulatedAgentHarness()
    linked = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=contact_payload,
            headers={},
        ),
        harness,
    )
    assert isinstance(linked, SurfaceReplyContext)
    assert "linked now" in linked.reply_message

    chat = await _process_ingress_and_emulate_reply(
        db_session,
        SurfacePlatformWebhookIngress(
            source="telegram",
            payload=_telegram_payload(
                text="Now can you help?",
                message_id=502,
                sender_id=123123123,
            ),
            headers={},
        ),
        harness,
    )
    assert isinstance(chat, SurfaceChatContext)
    conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        external_thread_id="123123123",
    )
    assert conversation is not None
    telegram_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=2)
    # Text is rendered as MarkdownV2, so reserved chars ([ ] _) are escaped.
    assert "E2E agent reply" in telegram_messages[-1]["text"]
    assert "TELEGRAM" in telegram_messages[-1]["text"]
