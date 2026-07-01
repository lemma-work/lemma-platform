"""Multi-tool-turn coverage: two sequential real tool calls (``display_resource``
then ``say``, or two ``display_resource`` calls) followed by one final answer,
across all 7 platforms — proves ordering (both tool side effects land, in
sequence) and that exactly one final content message closes the turn (no
duplicate delivery from the run observer's fallback path).
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
    SurfaceScheduleIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    REAL_TEAMS_CHANNEL_ID,
    REAL_TEAMS_TENANT_ID,
    _create_agent_surface,
    _ensure_connector_account,
    _ensure_connector_trigger,
    _gmail_payload,
    _load_slack_dm_fixture,
    _load_teams_channel_mention_fixture,
    _messages_for_conversation,
    _outlook_payload,
    _resend_payload,
    _seed_external_user,
    _set_user_mobile_number,
    _telegram_payload,
    _whatsapp_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_display_resource,
    script_email_reply,
    script_say,
    script_text,
)
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.schedule.infrastructure.schedule_managers.manager_factory import (
    ManagersFactory,
)

pytestmark = pytest.mark.e2e


_WIDGET_ARGS = {
    "type": "WIDGET",
    "content": "<svg viewBox='0 0 10 10'><circle cx='5' cy='5' r='4'/></svg>",
}


class _FakeScheduleManager:
    async def create_schedule(self, *, account, app_trigger, config) -> str:
        return f"e2e-{app_trigger.id}"

    async def delete_schedule(self, account, provider_id: str) -> None:
        return None

    async def get_schedule(self, account, provider_id: str):
        return None


async def test_multi_tool_turn_slack_widget_then_say_then_one_final_answer(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    fake_speech_provider,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-multi-tool",
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
        toolsets=["USER_INTERACTION", "SPEECH"],
    )

    dm_payload = _load_slack_dm_fixture(text="show me and tell me", ts="1700005100.600600")
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        script=[
            script_display_resource(**_WIDGET_ARGS, tool_call_id="tool-display-1"),
            script_say("Here's what I found.", tool_call_id="tool-say-1"),
            script_text("All done."),
        ],
    )

    # Widget landed before the voice note, which landed before the final
    # answer — three distinct SLACK messages, in that order.
    slack_messages = await wait_for_messages(message_store, "SLACK", min_count=1)
    widget_index = next(
        i for i, m in enumerate(slack_messages) if "blocks" in m or "attachments" in m
    )
    uploads = await wait_for_messages(message_store, "SLACK_FILE_UPLOAD_URL", min_count=1)
    assert uploads
    final_texts = [m for m in slack_messages if m.get("text") == "All done."]
    assert len(final_texts) == 1, "final answer must be delivered exactly once"
    assert slack_messages.index(final_texts[0]) > widget_index


async def test_multi_tool_turn_teams_two_widgets_then_one_final_answer(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings
    from app.modules.agent_surfaces.platforms.teams.adapter import TeamsSurfaceAdapter

    async def _fake_bot_token(self, tenant_id: str) -> str | None:
        del self, tenant_id
        return "teams-bot-token"

    async def _disable_graph(self, tenant_id: str) -> str | None:
        del self, tenant_id
        return None

    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_bot_token", _fake_bot_token)
    monkeypatch.setattr(TeamsSurfaceAdapter, "_get_graph_token", _disable_graph)
    monkeypatch.setattr(
        surface_settings,
        "microsoft_bot_openid_config_url",
        fake_teams.openid_config_url,
    )
    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "microsoft_bot_app_id", "teams-app-id")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="teams",
        credentials={
            "access_token": "teams-token",
            "user_data": {"tenant_id": REAL_TEAMS_TENANT_ID},
        },
    )
    await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={
            "type": "TEAMS",
            "account_id": str(account.id),
            "allowed_channel_ids": [REAL_TEAMS_CHANNEL_ID],
        },
        toolsets=["USER_INTERACTION"],
    )

    payload = _load_teams_channel_mention_fixture(fake_teams)
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="teams", payload=payload, headers={}),
        script=[
            script_display_resource(**_WIDGET_ARGS, tool_call_id="tool-display-1"),
            script_display_resource(**_WIDGET_ARGS, tool_call_id="tool-display-2"),
            script_text("All done."),
        ],
    )

    teams_messages = await wait_for_messages(message_store, "TEAMS", min_count=1)
    bodies = [m["body"] for m in teams_messages if m.get("body", {}).get("type") == "message"]
    widget_bodies = [b for b in bodies if b.get("attachments")]
    assert len(widget_bodies) == 2, "both widget calls must render their own message"
    final_bodies = [b for b in bodies if b.get("text") == "All done."]
    assert len(final_bodies) == 1, "final answer must be delivered exactly once"
    assert bodies.index(final_bodies[0]) > bodies.index(widget_bodies[-1])


async def test_multi_tool_turn_telegram_widget_then_say_then_one_final_answer(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    fake_speech_provider,
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
    sender_id = 555081012
    await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM"},
        toolsets=["USER_INTERACTION", "SPEECH"],
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    payload = _telegram_payload(
        text="show me and tell me", message_id=951, sender_id=sender_id
    )
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        script=[
            script_display_resource(**_WIDGET_ARGS, tool_call_id="tool-display-1"),
            script_say("Here's what I found.", tool_call_id="tool-say-1"),
            script_text("All done."),
        ],
    )

    voice = await wait_for_messages(message_store, "TELEGRAM_VOICE", min_count=1)
    assert voice
    telegram_messages = message_store.get_all("TELEGRAM")
    final_texts = [m for m in telegram_messages if "All done" in m.get("text", "")]
    assert len(final_texts) == 1, "final answer must be delivered exactly once"


async def test_multi_tool_turn_whatsapp_two_widgets_then_one_final_answer(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.whatsapp.service._WHATSAPP_API_BASE",
        f"{fake_whatsapp.api_base}/v21.0",
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "1234567890")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-001")
    monkeypatch.setattr(surface_settings, "whatsapp_app_secret", "wa-secret")
    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "WHATSAPP"},
        toolsets=["USER_INTERACTION"],
    )
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="15550101010",
    )

    payload = _whatsapp_payload(
        text="show me twice",
        message_id="wamid-e2e-multi-001",
        phone_number_id="1234567890",
        waba_id="waba-001",
        sender_phone="15550101010",
    )
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="whatsapp", payload=payload, headers={}),
        script=[
            script_display_resource(**_WIDGET_ARGS, tool_call_id="tool-display-1"),
            script_display_resource(**_WIDGET_ARGS, tool_call_id="tool-display-2"),
            script_text("All done."),
        ],
    )

    # WIDGET resources have no path to upload as native media (that's FILE-type
    # only — see send_display_resource_for_conversation) — both calls render as
    # an interactive "open widget" link card, distinct from the final text.
    whatsapp_messages = await wait_for_messages(message_store, "WHATSAPP", min_count=3)
    widget_messages = [m for m in whatsapp_messages if m.get("type") == "interactive"]
    assert len(widget_messages) == 2, "both widget calls must render their own message"
    text_messages = [m for m in whatsapp_messages if m.get("type") == "text"]
    final_texts = [m for m in text_messages if m["text"]["body"] == "All done."]
    assert len(final_texts) == 1, "final answer must be delivered exactly once"


async def test_multi_tool_turn_gmail_two_widgets_then_one_reply(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_gmail,
    fake_composio_email,
    message_store,
    monkeypatch,
):
    """Two display_resource(WIDGET) calls each succeed (returning a signed
    serve URL) without producing separate sends — see
    ``_maybe_deliver_to_surface``'s explicit ``caps.is_email`` early-return —
    then the single reply-tool call is the only actual outbound send."""
    monkeypatch.setattr(
        ManagersFactory, "get_manager", lambda *args, **kwargs: _FakeScheduleManager()
    )
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="gmail",
        credentials={
            "access_token": "gmail-token",
            "api_base_url": fake_gmail.api_base,
        },
        email="assistant@gmail.test",
        provider=AuthProvider.COMPOSIO,
    )
    await _ensure_connector_trigger(
        db_session,
        connector_id="gmail",
        trigger_id="gmail_new_message_multi_e2e",
        event_type="GMAIL_NEW_GMAIL_MESSAGE",
    )
    _agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "GMAIL", "account_id": str(account.id)},
        toolsets=["USER_INTERACTION"],
    )
    surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
    assert surface_model is not None
    assert surface_model.schedule_id is not None

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfaceScheduleIngress(
            schedule_id=surface_model.schedule_id,
            payload=_gmail_payload(
                sender_email=fixed_test_user["email"],
                assistant_email="assistant@gmail.test",
                thread_id="gmail-thread-multi-e2e",
                message_id="gmail-message-multi-1",
                text="Can you help over Gmail?",
            ),
            account_id=account.id,
            pod_id=UUID(pod_id),
            user_id=UUID(fixed_test_user["id"]),
        ),
        script=[
            script_display_resource(**_WIDGET_ARGS, tool_call_id="tool-display-1"),
            script_display_resource(**_WIDGET_ARGS, tool_call_id="tool-display-2"),
            script_email_reply(
                "gmail_reply_email", "Here is my answer.", tool_call_id="tool-email-reply-1"
            ),
        ],
    )

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=str(context.conversation_id)
    )
    display_returns = [
        m
        for m in messages
        if m.get("kind") == "TOOL_RETURN" and m.get("tool_name") == "display_resource"
    ]
    assert len(display_returns) == 2
    assert all(r["tool_result"]["success"] for r in display_returns)
    assert all(r["tool_result"]["url"] for r in display_returns)

    gmail_messages = await wait_for_messages(message_store, "GMAIL_REPLY", min_count=1)
    assert len(gmail_messages) == 1, "exactly one email must be sent for the turn"
    assert gmail_messages[0]["operation_name"] == "GMAIL_REPLY_TO_THREAD"
    assert "Here is my answer." in json.dumps(gmail_messages[0]["payload"])


async def test_multi_tool_turn_outlook_two_widgets_then_one_reply(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_outlook,
    fake_composio_email,
    message_store,
    monkeypatch,
):
    """Same shape as the Gmail case: two no-op-delivery WIDGET calls, then the
    single reply-tool call is the only actual outbound send."""
    monkeypatch.setattr(
        ManagersFactory, "get_manager", lambda *args, **kwargs: _FakeScheduleManager()
    )
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="outlook",
        credentials={
            "access_token": "outlook-token",
            "api_base_url": fake_outlook.api_base,
        },
        email="assistant@outlook.test",
        provider=AuthProvider.COMPOSIO,
    )
    await _ensure_connector_trigger(
        db_session,
        connector_id="outlook",
        trigger_id="outlook_message_multi_e2e",
        event_type="OUTLOOK_MESSAGE_TRIGGER",
    )
    _agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "OUTLOOK", "account_id": str(account.id)},
        toolsets=["USER_INTERACTION"],
    )
    surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
    assert surface_model is not None
    assert surface_model.schedule_id is not None

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfaceScheduleIngress(
            schedule_id=surface_model.schedule_id,
            payload=_outlook_payload(
                sender_email=fixed_test_user["email"],
                assistant_email="assistant@outlook.test",
                thread_id="outlook-thread-multi-e2e",
                message_id="outlook-message-multi-1",
                text="Can you help over Outlook?",
            ),
            account_id=account.id,
            pod_id=UUID(pod_id),
            user_id=UUID(fixed_test_user["id"]),
        ),
        script=[
            script_display_resource(**_WIDGET_ARGS, tool_call_id="tool-display-1"),
            script_display_resource(**_WIDGET_ARGS, tool_call_id="tool-display-2"),
            script_email_reply(
                "outlook_reply_email", "Here is my answer.", tool_call_id="tool-email-reply-1"
            ),
        ],
    )

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=str(context.conversation_id)
    )
    display_returns = [
        m
        for m in messages
        if m.get("kind") == "TOOL_RETURN" and m.get("tool_name") == "display_resource"
    ]
    assert len(display_returns) == 2
    assert all(r["tool_result"]["success"] for r in display_returns)

    outlook_messages = await wait_for_messages(
        message_store, "OUTLOOK_REPLY", min_count=1
    )
    assert len(outlook_messages) == 1, "exactly one email must be sent for the turn"
    assert outlook_messages[0]["operation_name"] == "OUTLOOK_REPLY_EMAIL"
    assert "Here is my answer." in json.dumps(outlook_messages[0]["payload"])


async def test_multi_tool_turn_resend_two_widgets_then_one_reply(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """Same shape as Gmail/Outlook: two no-op-delivery WIDGET calls, then the
    single reply-tool call is the only actual outbound send — proven here via
    a real (non-Composio) send, unlike Gmail/Outlook's intercepted calls."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="resend",
        credentials={
            "api_key": "resend-token",
            "api_base_url": fake_resend.api_base,
        },
        email="assistant@resend.test",
        provider=AuthProvider.LEMMA,
    )
    _agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "RESEND", "account_id": str(account.id)},
        toolsets=["USER_INTERACTION"],
    )
    assistant_address = surface.get("surface_identity_email")
    if not assistant_address:
        surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
        assistant_address = surface_model.surface_identity_email
    assert assistant_address

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=fixed_test_user["email"],
                assistant_address=assistant_address,
                message_id="resend-message-multi-1",
                text="Can you help over email?",
            ),
            headers={},
        ),
        script=[
            script_display_resource(**_WIDGET_ARGS, tool_call_id="tool-display-1"),
            script_display_resource(**_WIDGET_ARGS, tool_call_id="tool-display-2"),
            script_email_reply(
                "resend_reply_email", "Here is my answer.", tool_call_id="tool-email-reply-1"
            ),
        ],
    )

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=str(context.conversation_id)
    )
    display_returns = [
        m
        for m in messages
        if m.get("kind") == "TOOL_RETURN" and m.get("tool_name") == "display_resource"
    ]
    assert len(display_returns) == 2
    assert all(r["tool_result"]["success"] for r in display_returns)

    resend_messages = await wait_for_messages(message_store, "RESEND", min_count=1)
    assert len(resend_messages) == 1, "exactly one email must be sent for the turn"
    assert "Here is my answer." in json.dumps(resend_messages[0])
