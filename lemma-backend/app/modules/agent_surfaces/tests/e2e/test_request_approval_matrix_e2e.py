"""request_approval tool-coverage matrix: text-prompt render + typed
"approve"/"deny" resume, across every chat platform (Slack, Teams, Telegram,
WhatsApp), plus negative cases proving the tool is suppressed on email
surfaces (Gmail, Outlook, Resend) — email is non-interactive, so the agent
must complete via its single reply-tool call instead of ever pausing.

Unlike ``ask_user``, ``request_approval`` has NO native rendering on any
platform: the prompt is always a plain text message
(``send_approval_prompt_for_conversation`` in ``ingress_service.py``), and the
answer always arrives as an ordinary typed reply ("approve"/"deny") resolved
by ``_maybe_resume_pending_interaction`` — the SAME code path as any other
inbound chat message, not a native form submission. So every platform's
resume step is identical: send another ordinary inbound message through
``process_ingress_and_run_scripted`` with the typed decision text.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.infrastructure.models import AgentModel
from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfacePlatformWebhookIngress,
    SurfaceScheduleIngress,
)
from app.modules.agent_surfaces.infrastructure.models import AgentSurface
from app.modules.agent_surfaces.tests.e2e.helpers import (
    E2E_RUNTIME_MODEL_NAME,
    REAL_TEAMS_CHANNEL_ID,
    REAL_TEAMS_TENANT_ID,
    _create_agent_surface,
    _ensure_connector_account,
    _ensure_connector_trigger,
    _ensure_e2e_runtime_profile,
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
    script_email_reply,
    script_request_approval,
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


_TOOL_CALL_ID = "tool-approval-1"
_INNER_TOOL_ARGS = {
    "type": "WIDGET",
    "content": "<svg viewBox='0 0 10 10'><circle cx='5' cy='5' r='4'/></svg>",
}


def _approval_script(final_text: str) -> list:
    return [
        script_request_approval(
            tool_name="display_resource",
            args={"request": _INNER_TOOL_ARGS},
            title="Show a widget",
            reason="Needs your OK first",
            tool_call_id=_TOOL_CALL_ID,
        ),
        script_text(final_text),
    ]


async def _make_approved_tool_resolvable(
    db_session: AsyncSession, *, agent_id: str, organization_id: str
) -> None:
    """Point the agent's OWN ``agent_runtime`` at the fake e2e runtime profile.

    ``request_approval``'s wrapped-tool execution (``ApprovalExecutor``) runs
    independently of the scripted-mock harness run and resolves the AGENT's
    (not the paused RUN's) runtime profile to decide tool availability — the
    default test agent's "system:fireworks" profile isn't configured in this
    e2e environment, so that resolution must be redirected too.
    """
    runtime_profile_id = await _ensure_e2e_runtime_profile(
        db_session, organization_id=UUID(organization_id)
    )
    agent = await db_session.get(AgentModel, UUID(agent_id))
    assert agent is not None
    agent.agent_runtime = {
        "profile_id": runtime_profile_id,
        "model_name": E2E_RUNTIME_MODEL_NAME,
    }
    await db_session.commit()


async def test_request_approval_slack_text_prompt_then_resumes_on_approve(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    fake_slack,
    message_store,
    monkeypatch,
):
    """request_approval renders as a plain text prompt (no native card); a
    typed "approve" reply resumes with a REAL RequestApprovalResponse."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-approval-matrix",
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
    await _make_approved_tool_resolvable(
        db_session, agent_id=agent["id"], organization_id=fixed_test_org["id"]
    )

    dm_payload = _load_slack_dm_fixture(
        text="please show the widget", ts="1700000100.600600"
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        script=_approval_script("Done — approved and executed."),
    )
    assert isinstance(context, SurfaceChatContext)
    conversation_id = str(context.conversation_id)
    sender_id = dm_payload["event"]["user"]
    channel_id = dm_payload["event"]["channel"]

    slack_messages = await wait_for_messages(message_store, "SLACK", min_count=1)
    prompt = slack_messages[-1]["text"]
    assert "Approval needed: Show a widget" in prompt
    assert "Needs your OK first" in prompt
    assert "Action: display_resource" in prompt
    assert 'Reply "approve"' in prompt
    # No native structure of any kind — request_approval never renders one.
    assert "blocks" not in slack_messages[-1]

    # A Slack DM's conversation-link identity is keyed by thread_ts-or-ts (see
    # SlackMessageParser), so the "approve" reply must carry the ORIGINAL
    # message's ts as thread_ts to resolve back to the same conversation
    # instead of starting a new one.
    approve_payload = _load_slack_dm_fixture(
        text="approve",
        ts="1700000200.700700",
        thread_ts="1700000100.600600",
    )
    approve_payload["event"]["user"] = sender_id
    approve_payload["event"]["channel"] = channel_id
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=approve_payload, headers={}),
        script=None,
    )

    slack_messages = await wait_for_messages(message_store, "SLACK", min_count=2)
    assert "Done — approved and executed." in slack_messages[-1]["text"]

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=conversation_id
    )
    tool_return = next(
        m
        for m in messages
        if m.get("tool_call_id") == _TOOL_CALL_ID and m.get("kind") == "TOOL_RETURN"
    )
    result = tool_return["tool_result"]
    assert result["decision"] == "APPROVE_ONCE"
    assert result["executed"] is True


async def test_request_approval_slack_typed_deny_skips_wrapped_tool(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
    fake_slack,
    message_store,
    monkeypatch,
):
    """A typed "deny" reply resolves DENY and never runs the wrapped tool."""
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-approval-deny",
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
    await _make_approved_tool_resolvable(
        db_session, agent_id=agent["id"], organization_id=fixed_test_org["id"]
    )

    dm_payload = _load_slack_dm_fixture(
        text="please show the widget", ts="1700001100.600600"
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        script=_approval_script("Okay, cancelled."),
    )
    assert isinstance(context, SurfaceChatContext)
    conversation_id = str(context.conversation_id)
    sender_id = dm_payload["event"]["user"]
    channel_id = dm_payload["event"]["channel"]

    await wait_for_messages(message_store, "SLACK", min_count=1)

    deny_payload = _load_slack_dm_fixture(
        text="deny",
        ts="1700001200.700700",
        thread_ts="1700001100.600600",
    )
    deny_payload["event"]["user"] = sender_id
    deny_payload["event"]["channel"] = channel_id
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=deny_payload, headers={}),
        script=None,
    )

    slack_messages = await wait_for_messages(message_store, "SLACK", min_count=2)
    assert "Okay, cancelled." in slack_messages[-1]["text"]

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=conversation_id
    )
    tool_return = next(
        m
        for m in messages
        if m.get("tool_call_id") == _TOOL_CALL_ID and m.get("kind") == "TOOL_RETURN"
    )
    result = tool_return["tool_result"]
    assert result["decision"] == "DENY"
    assert result["executed"] is False
    assert result["result"] is None


async def test_request_approval_teams_text_prompt_then_resumes_on_approve(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
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
    await _make_approved_tool_resolvable(
        db_session, agent_id=agent["id"], organization_id=fixed_test_org["id"]
    )

    payload = _load_teams_channel_mention_fixture(fake_teams)
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="teams", payload=payload, headers={}),
        script=_approval_script("Done — approved and executed."),
    )
    assert isinstance(context, SurfaceChatContext)
    conversation_id = str(context.conversation_id)

    teams_messages = await wait_for_messages(message_store, "TEAMS", min_count=1)
    prompt_bodies = [
        item["body"]
        for item in teams_messages
        if item.get("body", {}).get("type") == "message"
    ]
    assert "Approval needed: Show a widget" in prompt_bodies[-1].get("text", "")

    # A Teams channel reply's external_thread_id is the root message's own id
    # (see TeamsMessageParser._parse_bot_framework_message) — set replyToId to
    # that root id so "approve" resolves back to the same conversation.
    approve_payload = dict(payload)
    approve_payload["id"] = "1776236638099"
    approve_payload["replyToId"] = payload["id"]
    approve_payload["text"] = "approve"
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="teams", payload=approve_payload, headers={}),
        script=None,
    )

    teams_messages = await wait_for_messages(message_store, "TEAMS", min_count=2)
    text_bodies = [
        item["body"]
        for item in teams_messages
        if item.get("body", {}).get("type") == "message"
    ]
    assert "Done — approved and executed." in text_bodies[-1].get("text", "")

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=conversation_id
    )
    tool_return = next(
        m
        for m in messages
        if m.get("tool_call_id") == _TOOL_CALL_ID and m.get("kind") == "TOOL_RETURN"
    )
    result = tool_return["tool_result"]
    assert result["decision"] == "APPROVE_ONCE"
    assert result["executed"] is True


async def test_request_approval_telegram_text_prompt_then_resumes_on_approve(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
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
    sender_id = 555020304
    agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM"},
        toolsets=["USER_INTERACTION"],
    )
    await _make_approved_tool_resolvable(
        db_session, agent_id=agent["id"], organization_id=fixed_test_org["id"]
    )
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    payload = _telegram_payload(
        text="please show the widget", message_id=911, sender_id=sender_id
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        script=_approval_script("Done — approved and executed."),
    )
    assert isinstance(context, SurfaceChatContext)
    conversation_id = str(context.conversation_id)

    telegram_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    assert any(
        "Approval needed: Show a widget" in m.get("text", "") for m in telegram_messages
    )

    # A Telegram private chat's external_thread_id is the stable chat_id, so a
    # follow-up message from the same sender naturally continues the same
    # conversation — no explicit threading needed.
    approve_payload = _telegram_payload(
        text="approve", message_id=912, sender_id=sender_id
    )
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=approve_payload, headers={}),
        script=None,
    )

    telegram_messages = message_store.get_all("TELEGRAM")
    assert any(
        "Done" in m.get("text", "") and "approved and executed" in m.get("text", "")
        for m in telegram_messages
    )

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=conversation_id
    )
    tool_return = next(
        m
        for m in messages
        if m.get("tool_call_id") == _TOOL_CALL_ID and m.get("kind") == "TOOL_RETURN"
    )
    result = tool_return["tool_result"]
    assert result["decision"] == "APPROVE_ONCE"
    assert result["executed"] is True


async def test_request_approval_whatsapp_text_prompt_then_resumes_on_approve(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fixed_test_org,
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
    agent, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "WHATSAPP"},
        toolsets=["USER_INTERACTION"],
    )
    await _make_approved_tool_resolvable(
        db_session, agent_id=agent["id"], organization_id=fixed_test_org["id"]
    )
    await _set_user_mobile_number(
        db_session,
        user_id=fixed_test_user["id"],
        mobile_number="15550777777",
    )

    payload = _whatsapp_payload(
        text="please show the widget",
        message_id="wamid-e2e-approval-001",
        phone_number_id="1234567890",
        waba_id="waba-001",
        sender_phone="15550777777",
    )
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="whatsapp", payload=payload, headers={}),
        script=_approval_script("Done — approved and executed."),
    )
    assert isinstance(context, SurfaceChatContext)
    conversation_id = str(context.conversation_id)

    whatsapp_messages = await wait_for_messages(message_store, "WHATSAPP", min_count=1)
    text_messages = [m for m in whatsapp_messages if m.get("type") == "text"]
    assert "Approval needed: Show a widget" in text_messages[-1]["text"]["body"]

    # A WhatsApp thread's external_thread_id is the stable sender@phone_number_id
    # pair, so a follow-up message naturally continues the same conversation.
    approve_payload = _whatsapp_payload(
        text="approve",
        message_id="wamid-e2e-approval-002",
        phone_number_id="1234567890",
        waba_id="waba-001",
        sender_phone="15550777777",
    )
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="whatsapp", payload=approve_payload, headers={}),
        script=None,
    )

    whatsapp_messages = await wait_for_messages(message_store, "WHATSAPP", min_count=2)
    text_messages = [m for m in whatsapp_messages if m.get("type") == "text"]
    assert "Done — approved and executed." in text_messages[-1]["text"]["body"]

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=conversation_id
    )
    tool_return = next(
        m
        for m in messages
        if m.get("tool_call_id") == _TOOL_CALL_ID and m.get("kind") == "TOOL_RETURN"
    )
    result = tool_return["tool_result"]
    assert result["decision"] == "APPROVE_ONCE"
    assert result["executed"] is True


async def test_request_approval_suppressed_on_gmail_reply_completes_via_reply_tool(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_gmail,
    fake_composio_email,
    message_store,
    monkeypatch,
):
    """Email surfaces never offer request_approval (agent has no
    USER_INTERACTION toolset) — the agent must complete via its reply tool."""
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
        trigger_id="gmail_new_message_approval_e2e",
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
                thread_id="gmail-thread-approval-e2e",
                message_id="gmail-message-approval-1",
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


async def test_request_approval_suppressed_on_outlook_reply_completes_via_reply_tool(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_outlook,
    fake_composio_email,
    message_store,
    monkeypatch,
):
    """Email surfaces never offer request_approval (agent has no
    USER_INTERACTION toolset) — the agent must complete via its reply tool."""
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
        trigger_id="outlook_message_approval_e2e",
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
                thread_id="outlook-thread-approval-e2e",
                message_id="outlook-message-approval-1",
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


async def test_request_approval_suppressed_on_resend_reply_completes_via_reply_tool(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """Email surfaces never offer request_approval (agent has no
    USER_INTERACTION toolset) — the agent must complete via its reply tool."""
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
                message_id="resend-message-approval-1",
                text="Can you help over email?",
            ),
            headers={},
        ),
        script=[script_email_reply("resend_reply_email", "Here is my answer.")],
    )

    resend_messages = await wait_for_messages(message_store, "RESEND", min_count=1)
    assert "Here is my answer." in json.dumps(resend_messages[-1])
