"""ask_user tool-coverage matrix: native rendering + resume, across every
platform that supports native choices (Slack, Teams, Telegram, WhatsApp), plus
negative cases proving the tool is suppressed on email surfaces (Gmail,
Outlook, Resend) where the agent must complete via its single reply-tool call
instead of ever pausing on a question.

Unlike the old ``AskUserHarness`` (which hand-crafted a WAITING ``AgentEvent``
without ever calling the real tool), these tests script the LLM only — the
real ``ask_user`` tool runs for real, genuinely raises ``AgentInputRequired``,
and the synthesized ``AskUserResponse`` genuinely flows back through history.
This is what proves the mechanism (real harness + mock_llm_script) end-to-end
before it's reused for every other tool-matrix file.
"""

from __future__ import annotations

import json
import urllib.parse
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
    SurfaceScheduleIngress,
)
from app.modules.agent_surfaces.events.handlers import build_surface_event_handler
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.tests.e2e.helpers import (
    REAL_TEAMS_CHANNEL_ID,
    REAL_TEAMS_TENANT_ID,
    REAL_TEAMS_THREAD_ID,
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
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_slack_signature_headers,
    build_telegram_secret_headers,
    build_whatsapp_signature_headers,
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    resume_latest_scripted_run,
    script_ask_user,
    script_email_reply,
    script_text,
)
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.schedule.infrastructure.schedule_managers.manager_factory import (
    ManagersFactory,
)

pytestmark = pytest.mark.e2e


class _FakeScheduleManager:
    async def create_schedule(self, *, account, app_trigger, config) -> str:
        return f"e2e-{app_trigger.id}"

    async def delete_schedule(self, account, provider_id: str) -> None:
        return None

    async def get_schedule(self, account, provider_id: str):
        return None


_QUESTIONS = [
    {
        "question": "Pick a color",
        "header": "color",
        "options": [{"label": "Red"}, {"label": "Blue"}],
    }
]
_TOOL_CALL_ID = "tool-ask-1"


def _slack_ask_user_submission_payload(
    *, callback_id: str, user_id: str, channel_id: str, header: str, label: str
) -> dict:
    """A Slack block_actions submission answering a native ask_user question.

    The native render keys the select by the question header (block_id) and
    uses the option label as its value, so the answer flattens to
    ``{header: label}``.
    """
    return {
        "type": "block_actions",
        "user": {"id": user_id},
        "team": {"id": "T0123456"},
        "channel": {"id": channel_id},
        "container": {"message_ts": "1700000000.700700"},
        "message": {"ts": "1700000000.700700"},
        "actions": [
            {
                "action_id": "lemma_form_submit",
                "value": callback_id,
                "action_ts": "1700000000.700800",
            }
        ],
        "state": {
            "values": {
                header: {
                    header: {
                        "type": "static_select",
                        "selected_option": {"value": label},
                    }
                }
            }
        },
    }


async def test_ask_user_native_slack_blocks_then_resumes_with_answer(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    """ask_user renders as native Slack choices; a block_actions answer resumes
    the paused run with a REAL, structured ``AskUserResponse``."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-ask-matrix",
            "scope": "chat:write",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
        toolsets=["USER_INTERACTION"],
    )

    dm_payload = _load_slack_dm_fixture(text="which color?", ts="1700000000.600600")
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        script=[
            script_ask_user(_QUESTIONS, tool_call_id=_TOOL_CALL_ID),
            script_text("Thanks — recorded your answer."),
        ],
    )
    assert isinstance(context, SurfaceChatContext)
    conversation_id = str(context.conversation_id)
    sender_id = dm_payload["event"]["user"]
    channel_id = dm_payload["event"]["channel"]

    # The real ask_user tool call was rendered as a native Slack select — not a
    # plain-text question.
    slack_messages = await wait_for_messages(message_store, "SLACK", min_count=1)
    rendered = json.dumps(slack_messages)
    assert "Pick a color" in rendered
    assert "Blue" in rendered and "static_select" in rendered

    submission = _slack_ask_user_submission_payload(
        callback_id=f"{conversation_id}|{_TOOL_CALL_ID}",
        user_id=sender_id,
        channel_id=channel_id,
        header="color",
        label="Blue",
    )
    form_body = urllib.parse.urlencode({"payload": json.dumps(submission)}).encode("utf-8")
    headers = build_slack_signature_headers(raw_body=form_body, signing_secret="slack-secret")
    headers["Content-Type"] = "application/x-www-form-urlencoded"
    resp = await authenticated_client.post(
        "/surfaces/webhooks/slack", content=form_body, headers=headers
    )
    assert resp.status_code == 200, resp.text

    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    handled = await handler.try_handle_interaction(
        SurfacePlatformWebhookIngress(source="slack", payload=submission, headers={})
    )
    assert handled is True
    await uow.commit()

    await resume_latest_scripted_run(
        db_session,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        pod_id=context.pod_id,
        agent_name=context.agent_name,
    )

    slack_messages = await wait_for_messages(message_store, "SLACK", min_count=2)
    assert "Thanks — recorded your answer." in slack_messages[-1]["text"]

    # Proof the mechanism gives that the old fake harness never could: the REAL
    # AskUserResponse shape flowed through persisted history.
    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=conversation_id
    )
    tool_return = next(
        m
        for m in messages
        if m.get("tool_call_id") == _TOOL_CALL_ID and m.get("kind") == "TOOL_RETURN"
    )
    assert tool_return["tool_result"]["answers"] == {"color": "Blue"}


async def test_ask_user_native_teams_adaptive_card_then_resumes_with_answer(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
):
    """ask_user renders as a native Teams Adaptive Card Input.ChoiceSet; an
    Action.Submit answer resumes the paused run with a REAL AskUserResponse."""
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
    agent, surface = await _create_agent_surface(
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
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="teams", payload=payload, headers={}),
        script=[
            script_ask_user(_QUESTIONS, tool_call_id=_TOOL_CALL_ID),
            script_text("Thanks — recorded your answer."),
        ],
    )
    assert isinstance(context, SurfaceChatContext)
    conversation_id = str(context.conversation_id)

    teams_messages = await wait_for_messages(message_store, "TEAMS", min_count=1)
    rendered = json.dumps(teams_messages)
    assert "Pick a color" in rendered
    assert "Input.ChoiceSet" in rendered and "Blue" in rendered

    submission = {
        "type": "message",
        "id": "teams-answer-activity-1",
        "serviceUrl": fake_teams.service_url,
        "from": payload["from"],
        "conversation": payload["conversation"],
        "channelData": payload["channelData"],
        "replyToId": REAL_TEAMS_THREAD_ID,
        "value": {
            "lemma_form_callback_id": f"{conversation_id}|{_TOOL_CALL_ID}",
            "color": "Blue",
        },
    }
    raw_body = json.dumps(submission).encode("utf-8")
    resp = await authenticated_client.post(
        "/surfaces/webhooks/teams",
        content=raw_body,
        headers={
            "Content-Type": "application/json",
            "Authorization": (
                "Bearer "
                f"{fake_teams.issue_webhook_token(audience='teams-app-id')}"
            ),
        },
    )
    assert resp.status_code == 200, resp.text

    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    handled = await handler.try_handle_interaction(
        SurfacePlatformWebhookIngress(source="teams", payload=submission, headers={})
    )
    assert handled is True
    await uow.commit()

    await resume_latest_scripted_run(
        db_session,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        pod_id=context.pod_id,
        agent_name=context.agent_name,
    )

    teams_messages = await wait_for_messages(message_store, "TEAMS", min_count=2)
    text_bodies = [
        item["body"]
        for item in teams_messages
        if item.get("body", {}).get("type") == "message"
    ]
    assert "Thanks — recorded your answer." in text_bodies[-1].get("text", "")

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=conversation_id
    )
    tool_return = next(
        m
        for m in messages
        if m.get("tool_call_id") == _TOOL_CALL_ID and m.get("kind") == "TOOL_RETURN"
    )
    assert tool_return["tool_result"]["answers"] == {"color": "Blue"}


async def test_ask_user_native_telegram_inline_keyboard_then_resumes_with_answer(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """ask_user renders as a native Telegram inline keyboard; a callback_query
    tap resumes the paused run with a REAL AskUserResponse."""
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "telegram_webhook_secret", "native-secret")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    pod_id = test_pod["id"]
    sender_id = 555010203
    await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM"},
        toolsets=["USER_INTERACTION"],
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    payload = _telegram_payload(
        text="which color?", message_id=901, sender_id=sender_id
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        script=[
            script_ask_user(_QUESTIONS, tool_call_id=_TOOL_CALL_ID),
            script_text("Thanks — recorded your answer."),
        ],
    )
    assert isinstance(context, SurfaceChatContext)
    conversation_id = str(context.conversation_id)
    pod_id_from_ctx = pod_id

    telegram_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    rendered = json.dumps(telegram_messages)
    assert "Pick a color" in rendered
    keyboard_message = next(m for m in telegram_messages if "reply_markup" in m)
    inline_keyboard = keyboard_message["reply_markup"]["inline_keyboard"]
    assert inline_keyboard[1][0]["text"] == "Blue"
    blue_token = inline_keyboard[1][0]["callback_data"]

    submission = {
        "update_id": 100501,
        "callback_query": {
            "id": "cbq-1",
            "from": {
                "id": sender_id,
                "is_bot": False,
                "first_name": "Surface",
                "username": "surfaceuser",
            },
            "message": {
                "message_id": 902,
                "chat": {"id": sender_id, "type": "private"},
                "date": 1700000200,
                "text": "Pick a color",
            },
            "chat_instance": "1234567890123456789",
            "data": blue_token,
        },
    }
    raw_body = json.dumps(submission).encode("utf-8")
    resp = await authenticated_client.post(
        "/surfaces/webhooks/telegram",
        content=raw_body,
        headers=build_telegram_secret_headers("native-secret"),
    )
    assert resp.status_code == 200, resp.text

    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    handled = await handler.try_handle_interaction(
        SurfacePlatformWebhookIngress(source="telegram", payload=submission, headers={})
    )
    assert handled is True
    await uow.commit()

    await resume_latest_scripted_run(
        db_session,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        pod_id=context.pod_id,
        agent_name=context.agent_name,
    )

    telegram_messages = message_store.get_all("TELEGRAM")
    # Telegram renders MarkdownV2, which escapes the trailing "." — match the
    # unescaped portion of the reply text only.
    assert any(
        "Thanks" in m.get("text", "") and "recorded your answer" in m.get("text", "")
        for m in telegram_messages
    )

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id_from_ctx, conversation_id=conversation_id
    )
    tool_return = next(
        m
        for m in messages
        if m.get("tool_call_id") == _TOOL_CALL_ID and m.get("kind") == "TOOL_RETURN"
    )
    assert tool_return["tool_result"]["answers"] == {"color": "Blue"}


async def test_ask_user_native_whatsapp_buttons_then_resumes_with_answer(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_whatsapp,
    message_store,
    monkeypatch,
):
    """ask_user renders as native WhatsApp reply buttons; a button_reply
    resumes the paused run with a REAL AskUserResponse."""
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
        mobile_number="15550555555",
    )

    payload = _whatsapp_payload(
        text="which color?",
        message_id="wamid-e2e-ask-001",
        phone_number_id="1234567890",
        waba_id="waba-001",
        sender_phone="15550555555",
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="whatsapp", payload=payload, headers={}),
        script=[
            script_ask_user(_QUESTIONS, tool_call_id=_TOOL_CALL_ID),
            script_text("Thanks — recorded your answer."),
        ],
    )
    assert isinstance(context, SurfaceChatContext)
    conversation_id = str(context.conversation_id)

    whatsapp_messages = await wait_for_messages(message_store, "WHATSAPP", min_count=1)
    interactive_messages = [
        m for m in whatsapp_messages if m.get("type") == "interactive"
    ]
    assert interactive_messages
    rendered = json.dumps(interactive_messages)
    assert "Pick a color" in rendered and "Blue" in rendered

    submission = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "waba-001",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"phone_number_id": "1234567890"},
                            "contacts": [
                                {
                                    "wa_id": "15550555555",
                                    "profile": {"name": "Surface Test User"},
                                }
                            ],
                            "messages": [
                                {
                                    "from": "15550555555",
                                    "id": "wamid-e2e-reply-001",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {
                                            "id": (
                                                f"{conversation_id}|{_TOOL_CALL_ID}"
                                                "~color~Blue"
                                            ),
                                            "title": "Blue",
                                        },
                                    },
                                    "timestamp": "1700000001",
                                }
                            ],
                        }
                    }
                ],
            }
        ],
    }
    raw_body = json.dumps(submission).encode("utf-8")
    resp = await authenticated_client.post(
        "/surfaces/webhooks/whatsapp",
        content=raw_body,
        headers=build_whatsapp_signature_headers(
            raw_body=raw_body, app_secret="wa-secret"
        ),
    )
    assert resp.status_code == 200, resp.text

    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    handled = await handler.try_handle_interaction(
        SurfacePlatformWebhookIngress(source="whatsapp", payload=submission, headers={})
    )
    assert handled is True
    await uow.commit()

    await resume_latest_scripted_run(
        db_session,
        conversation_id=context.conversation_id,
        user_id=context.user_id,
        pod_id=context.pod_id,
        agent_name=context.agent_name,
    )

    whatsapp_messages = await wait_for_messages(message_store, "WHATSAPP", min_count=2)
    text_messages = [m for m in whatsapp_messages if m.get("type") == "text"]
    assert "Thanks — recorded your answer." in text_messages[-1]["text"]["body"]

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=conversation_id
    )
    tool_return = next(
        m
        for m in messages
        if m.get("tool_call_id") == _TOOL_CALL_ID and m.get("kind") == "TOOL_RETURN"
    )
    assert tool_return["tool_result"]["answers"] == {"color": "Blue"}


async def test_ask_user_suppressed_on_gmail_reply_completes_via_reply_tool(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_gmail,
    fake_composio_email,
    message_store,
    monkeypatch,
):
    """Email surfaces never offer ask_user (agent has no USER_INTERACTION
    toolset) — the agent must complete via its single reply-tool call."""
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
        trigger_id="gmail_new_message_ask_user_e2e",
        event_type="GMAIL_NEW_GMAIL_MESSAGE",
    )
    _agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "GMAIL", "account_id": str(account.id)},
    )
    surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
    assert surface_model is not None
    assert surface_model.schedule_id is not None

    await process_ingress_and_run_scripted(
        db_session,
        SurfaceScheduleIngress(
            schedule_id=surface_model.schedule_id,
            payload=_gmail_payload(
                sender_email=fixed_test_user["email"],
                assistant_email="assistant@gmail.test",
                thread_id="gmail-thread-ask-user-e2e",
                message_id="gmail-message-ask-user-1",
                text="Can you help over Gmail?",
            ),
            account_id=account.id,
            pod_id=UUID(pod_id),
            user_id=UUID(fixed_test_user["id"]),
        ),
        script=[script_email_reply("gmail_reply_email", "Here is my answer.")],
    )

    gmail_messages = await wait_for_messages(message_store, "GMAIL_REPLY", min_count=1)
    reply = gmail_messages[-1]
    assert reply["operation_name"] == "GMAIL_REPLY_TO_THREAD"
    assert "Here is my answer." in json.dumps(reply["payload"])


async def test_ask_user_suppressed_on_outlook_reply_completes_via_reply_tool(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_outlook,
    fake_composio_email,
    message_store,
    monkeypatch,
):
    """Email surfaces never offer ask_user (agent has no USER_INTERACTION
    toolset) — the agent must complete via its single reply-tool call."""
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
        trigger_id="outlook_message_ask_user_e2e",
        event_type="OUTLOOK_MESSAGE_TRIGGER",
    )
    _agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "OUTLOOK", "account_id": str(account.id)},
    )
    surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
    assert surface_model is not None
    assert surface_model.schedule_id is not None

    await process_ingress_and_run_scripted(
        db_session,
        SurfaceScheduleIngress(
            schedule_id=surface_model.schedule_id,
            payload=_outlook_payload(
                sender_email=fixed_test_user["email"],
                assistant_email="assistant@outlook.test",
                thread_id="outlook-thread-ask-user-e2e",
                message_id="outlook-message-ask-user-1",
                text="Can you help over Outlook?",
            ),
            account_id=account.id,
            pod_id=UUID(pod_id),
            user_id=UUID(fixed_test_user["id"]),
        ),
        script=[script_email_reply("outlook_reply_email", "Here is my answer.")],
    )

    outlook_messages = await wait_for_messages(
        message_store, "OUTLOOK_REPLY", min_count=1
    )
    reply = outlook_messages[-1]
    assert reply["operation_name"] == "OUTLOOK_REPLY_EMAIL"
    assert "Here is my answer." in json.dumps(reply["payload"])


async def test_ask_user_suppressed_on_resend_reply_completes_via_reply_tool(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """Email surfaces never offer ask_user (agent has no USER_INTERACTION
    toolset) — the agent must complete via its single reply-tool call."""
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
    )
    assistant_address = surface.get("surface_identity_email")
    if not assistant_address:
        surface_model = await db_session.get(AgentSurface, UUID(surface["id"]))
        assistant_address = surface_model.surface_identity_email
    assert assistant_address

    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=fixed_test_user["email"],
                assistant_address=assistant_address,
                message_id="resend-message-ask-user-1",
                text="Can you help over email?",
            ),
            headers={},
        ),
        script=[script_email_reply("resend_reply_email", "Here is my answer.")],
    )

    resend_messages = await wait_for_messages(message_store, "RESEND", min_count=1)
    assert "Here is my answer." in json.dumps(resend_messages[-1])
