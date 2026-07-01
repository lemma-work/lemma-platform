"""display_resource(type=FILE) tool-coverage matrix: native attachment vs
link-card fallback by size, across Slack/Teams/Telegram/WhatsApp, plus
attachment_paths-on-reply-tool for Gmail/Outlook/Resend.

Supersedes ``test_surface_file_egress_e2e.py``, which called the surface
handler's delivery method directly — bypassing the real ``display_resource``
tool entirely. These tests script the tool as a genuine LLM tool call so the
tool's own size check and the surface's native-vs-link decision both run for
real.

N/A cells:
- **Teams has no native file-send implementation at all**
  (``TeamsSurfaceAdapter`` doesn't override ``send_file_attachment``, so the
  base adapter's stub always returns ``False``) — every Teams file, regardless
  of size, falls back to a link card. Only one Teams case is needed since
  there is no size-threshold behavior to prove.
- **WhatsApp's large-file link fallback isn't separately tested** — the
  size-threshold decision (``fits_inline``) is generic, platform-agnostic
  logic already proven on both Slack and Telegram; a third repetition would
  just re-prove the same shared code path.
- **Gmail/Outlook attachments never reach the recipient** — both use Composio
  in this matrix (matching the existing Gmail/Outlook e2e pattern), and
  Composio-connected Gmail/Outlook accounts don't support outbound
  attachments yet (see ``GmailPlatformService.reply_email`` /
  ``OutlookPlatformService.reply_email``): the tool call succeeds but reports
  ``attachment_count=0`` with an explanatory message. That is the real,
  current production behavior, so it's what's asserted here — not a
  successful delivery.
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
from app.modules.agent_surfaces.platforms.attachment_limits import (
    SURFACE_INLINE_SOFT_BYTE_CAP,
)
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
    _seed_pod_file,
    _set_user_mobile_number,
    _telegram_payload,
    _whatsapp_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_display_resource,
    script_email_reply,
    script_text,
)
from app.modules.connectors.domain.connector import AuthProvider
from app.modules.schedule.infrastructure.schedule_managers.manager_factory import (
    ManagersFactory,
)

pytestmark = pytest.mark.e2e


_TOOL_CALL_ID = "tool-display-1"


class _FakeScheduleManager:
    async def create_schedule(self, *, account, app_trigger, config) -> str:
        return f"e2e-{app_trigger.id}"

    async def delete_schedule(self, account, provider_id: str) -> None:
        return None

    async def get_schedule(self, account, provider_id: str):
        return None


async def test_display_resource_slack_small_file_attaches_natively(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
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
            "access_token": "xoxb-file-matrix",
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
        toolsets=["USER_INTERACTION"],
    )
    path = await _seed_pod_file(
        db_session,
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        name="small.pdf",
        content=b"%PDF-small",
    )

    dm_payload = _load_slack_dm_fixture(text="show the report", ts="1700002100.600600")
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        script=[
            script_display_resource(
                type="FILE", path=path, tool_call_id=_TOOL_CALL_ID
            ),
            script_text("Here you go."),
        ],
    )

    upload_urls = await wait_for_messages(
        message_store, "SLACK_FILE_UPLOAD_URL", min_count=1
    )
    assert upload_urls[-1]["filename"] == "small.pdf"
    completions = message_store.get_all("SLACK_FILE_COMPLETE")
    assert completions
    # No plain-text link fallback message was needed for the file itself.
    slack_messages = message_store.get_all("SLACK")
    assert not any("small.pdf" in m.get("text", "") for m in slack_messages)


async def test_display_resource_slack_large_file_falls_back_to_link(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(app_settings, "frontend_url", "https://app.example.test")
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-file-matrix-large",
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
        toolsets=["USER_INTERACTION"],
    )
    big = b"x" * (SURFACE_INLINE_SOFT_BYTE_CAP + 1024)
    path = await _seed_pod_file(
        db_session,
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        name="big.bin",
        content=big,
    )

    dm_payload = _load_slack_dm_fixture(text="show the big file", ts="1700002200.600600")
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        script=[
            script_display_resource(
                type="FILE", path=path, tool_call_id=_TOOL_CALL_ID
            ),
            script_text("Here's a link instead."),
        ],
    )

    # Never attempted a native upload — straight to the link card.
    assert message_store.get_all("SLACK_FILE_UPLOAD_URL") == []
    slack_messages = await wait_for_messages(message_store, "SLACK", min_count=1)
    rendered = " ".join(m.get("text", "") for m in slack_messages)
    assert "app.example.test" in rendered


async def test_display_resource_teams_file_always_falls_back_to_link(
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
    monkeypatch.setattr(app_settings, "frontend_url", "https://app.example.test")
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
    path = await _seed_pod_file(
        db_session,
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        name="small.pdf",
        content=b"%PDF-small",
    )

    payload = _load_teams_channel_mention_fixture(fake_teams)
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="teams", payload=payload, headers={}),
        script=[
            script_display_resource(
                type="FILE", path=path, tool_call_id=_TOOL_CALL_ID
            ),
            script_text("Here's a link instead."),
        ],
    )

    teams_messages = await wait_for_messages(message_store, "TEAMS", min_count=1)
    rendered = json.dumps(teams_messages)
    assert "app.example.test" in rendered


async def test_display_resource_telegram_small_file_attaches_natively(
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
    sender_id = 555030405
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
    path = await _seed_pod_file(
        db_session,
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        name="small.pdf",
        content=b"%PDF-small",
    )

    payload = _telegram_payload(
        text="show the report", message_id=921, sender_id=sender_id
    )
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        script=[
            script_display_resource(
                type="FILE", path=path, tool_call_id=_TOOL_CALL_ID
            ),
            script_text("Here you go."),
        ],
    )

    files = await wait_for_messages(message_store, "TELEGRAM_FILE", min_count=1)
    assert files[-1]["filename"] == "small.pdf"


async def test_display_resource_telegram_large_file_falls_back_to_link(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "frontend_url", "https://app.example.test")
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "telegram_webhook_secret", "native-secret")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    pod_id = test_pod["id"]
    sender_id = 555040506
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
    big = b"x" * (SURFACE_INLINE_SOFT_BYTE_CAP + 1024)
    path = await _seed_pod_file(
        db_session,
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        name="big.bin",
        content=big,
    )

    payload = _telegram_payload(
        text="show the big file", message_id=922, sender_id=sender_id
    )
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        script=[
            script_display_resource(
                type="FILE", path=path, tool_call_id=_TOOL_CALL_ID
            ),
            script_text("Here's a link instead."),
        ],
    )

    assert message_store.get_all("TELEGRAM_FILE") == []
    telegram_messages = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    rendered = " ".join(m.get("text", "") for m in telegram_messages)
    assert "app.example.test" in rendered


async def test_display_resource_whatsapp_small_file_attaches_natively(
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
        mobile_number="15550888888",
    )
    path = await _seed_pod_file(
        db_session,
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        name="small.pdf",
        content=b"%PDF-small",
    )

    payload = _whatsapp_payload(
        text="show the report",
        message_id="wamid-e2e-file-001",
        phone_number_id="1234567890",
        waba_id="waba-001",
        sender_phone="15550888888",
    )
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="whatsapp", payload=payload, headers={}),
        script=[
            script_display_resource(
                type="FILE", path=path, tool_call_id=_TOOL_CALL_ID
            ),
            script_text("Here you go."),
        ],
    )

    uploads = await wait_for_messages(message_store, "WHATSAPP_MEDIA_UPLOAD", min_count=1)
    assert uploads[-1]["filename"] == "small.pdf"
    whatsapp_messages = await wait_for_messages(message_store, "WHATSAPP", min_count=1)
    documents = [m for m in whatsapp_messages if m.get("type") == "document"]
    assert documents
    assert documents[-1]["document"]["filename"] == "small.pdf"


async def test_display_resource_gmail_attachment_paths_reports_unsupported(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_gmail,
    fake_composio_email,
    message_store,
    monkeypatch,
):
    """Composio-connected Gmail doesn't support outbound attachments yet — the
    reply tool must still succeed, but report attachment_count=0."""
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
        trigger_id="gmail_new_message_file_e2e",
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
    path = await _seed_pod_file(
        db_session,
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        name="small.pdf",
        content=b"%PDF-small",
    )

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfaceScheduleIngress(
            schedule_id=surface_model.schedule_id,
            payload=_gmail_payload(
                sender_email=fixed_test_user["email"],
                assistant_email="assistant@gmail.test",
                thread_id="gmail-thread-file-e2e",
                message_id="gmail-message-file-1",
                text="Can you send me the report?",
            ),
            account_id=account.id,
            pod_id=UUID(pod_id),
            user_id=UUID(fixed_test_user["id"]),
        ),
        script=[
            script_email_reply(
                "gmail_reply_email",
                "Here is the report.",
                attachment_paths=[path],
                tool_call_id=_TOOL_CALL_ID,
            )
        ],
    )

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=str(context.conversation_id)
    )
    tool_return = next(
        m
        for m in messages
        if m.get("tool_call_id") == _TOOL_CALL_ID and m.get("kind") == "TOOL_RETURN"
    )
    result = tool_return["tool_result"]
    assert result["success"] is True
    assert result["attachment_count"] == 0
    assert "not" in result["message"].lower()

    gmail_messages = await wait_for_messages(message_store, "GMAIL_REPLY", min_count=1)
    assert "Here is the report." in json.dumps(gmail_messages[-1]["payload"])


async def test_display_resource_outlook_attachment_paths_reports_unsupported(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_outlook,
    fake_composio_email,
    message_store,
    monkeypatch,
):
    """Composio-connected Outlook doesn't support outbound attachments yet —
    the reply tool must still succeed, but report attachment_count=0."""
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
        trigger_id="outlook_message_file_e2e",
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
    path = await _seed_pod_file(
        db_session,
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        name="small.pdf",
        content=b"%PDF-small",
    )

    context = await process_ingress_and_run_scripted(
        db_session,
        SurfaceScheduleIngress(
            schedule_id=surface_model.schedule_id,
            payload=_outlook_payload(
                sender_email=fixed_test_user["email"],
                assistant_email="assistant@outlook.test",
                thread_id="outlook-thread-file-e2e",
                message_id="outlook-message-file-1",
                text="Can you send me the report?",
            ),
            account_id=account.id,
            pod_id=UUID(pod_id),
            user_id=UUID(fixed_test_user["id"]),
        ),
        script=[
            script_email_reply(
                "outlook_reply_email",
                "Here is the report.",
                attachment_paths=[path],
                tool_call_id=_TOOL_CALL_ID,
            )
        ],
    )

    messages = await _messages_for_conversation(
        authenticated_client, pod_id=pod_id, conversation_id=str(context.conversation_id)
    )
    tool_return = next(
        m
        for m in messages
        if m.get("tool_call_id") == _TOOL_CALL_ID and m.get("kind") == "TOOL_RETURN"
    )
    result = tool_return["tool_result"]
    assert result["success"] is True
    assert result["attachment_count"] == 0
    assert "not" in result["message"].lower()

    outlook_messages = await wait_for_messages(
        message_store, "OUTLOOK_REPLY", min_count=1
    )
    assert "Here is the report." in json.dumps(outlook_messages[-1]["payload"])


async def test_display_resource_resend_attachment_paths_delivers_real_attachment(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_resend,
    message_store,
    monkeypatch,
):
    """Resend is not Composio-gated — attachment_paths bytes genuinely reach
    the outbound email as a base64 attachment."""
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
    path = await _seed_pod_file(
        db_session,
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        name="small.pdf",
        content=b"%PDF-small",
    )

    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="resend",
            payload=_resend_payload(
                sender_email=fixed_test_user["email"],
                assistant_address=assistant_address,
                message_id="resend-message-file-1",
                text="Can you send me the report?",
            ),
            headers={},
        ),
        script=[
            script_email_reply(
                "resend_reply_email",
                "Here is the report.",
                attachment_paths=[path],
                tool_call_id=_TOOL_CALL_ID,
            )
        ],
    )

    resend_messages = await wait_for_messages(message_store, "RESEND", min_count=1)
    attachments = resend_messages[-1].get("attachments") or []
    assert attachments
    assert attachments[0]["filename"] == "small.pdf"
