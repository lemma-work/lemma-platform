"""Progress-streaming tool-coverage matrix: tool-call activity renders as a
live, edited message on Slack (chat.update), Telegram (editMessageText), and
Teams (PUT activity) — the three platforms in
``progress_observer._STREAM_PROGRESS_PLATFORMS``.

Each scripted tool call carries a ``comment`` (nested under ``request``, since
every platform tool takes a single ``request: Model`` parameter and no such
model uses ``extra="forbid"`` — see ``script_progress``'s docstring). The
progress observer reads that comment straight off the persisted (pre-tool-
execution) event to drive the live status text, independent of whatever the
wrapped tool itself returns.

N/A cells (see ``_STREAM_PROGRESS_PLATFORMS``/``_TEXT_PROGRESS_PLATFORMS`` in
``progress_observer.py``):
- **WhatsApp has no message-edit API** — it gets no per-step progress at all
  (only the inbound reaction/typing indicator signals work is happening).
- **Email gets one composed reply, never a stream** — Gmail/Outlook/Resend
  recipients would find a live-editing inbox message bizarre; the observer
  intentionally skips streaming there regardless of platform capability.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent_surfaces.domain.ingress_request import SurfacePlatformWebhookIngress
from app.modules.agent_surfaces.tests.e2e.helpers import (
    REAL_TEAMS_CHANNEL_ID,
    REAL_TEAMS_TENANT_ID,
    _create_agent_surface,
    _ensure_connector_account,
    _load_slack_dm_fixture,
    _load_teams_channel_mention_fixture,
    _seed_external_user,
    _telegram_payload,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import wait_for_messages
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_progress,
)

pytestmark = pytest.mark.e2e


async def test_progress_streams_via_chat_update_on_slack(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    """Tool activity streams as an edited Slack message (chat.update) and the
    placeholder is deleted before the final answer."""
    from app.core.config import settings as app_settings
    from app.modules.agent_surfaces.services import progress_observer as _po

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    monkeypatch.setattr(surface_settings, "slack_signing_secret", "slack-secret")
    # Disable the inter-update throttle so both progress comments stream.
    monkeypatch.setattr(_po, "_MIN_TEXT_PROGRESS_INTERVAL_SECONDS", 0.0)
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-progress-matrix",
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
    )

    dm_payload = _load_slack_dm_fixture(text="do some work", ts="1700004100.600600")
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        script=script_progress(
            ["Searching the web", "Reading the results"],
            final_text="Here is the answer.",
            tool_name="slack_get_recent_channel_messages",
        ),
    )

    updates = await wait_for_messages(message_store, "SLACK_UPDATE", min_count=1)
    assert any("Reading the results" in json.dumps(u) for u in updates)
    deletes = await wait_for_messages(message_store, "SLACK_DELETE", min_count=1)
    assert deletes
    final = await wait_for_messages(message_store, "SLACK", min_count=1)
    assert "Here is the answer." in final[-1]["text"]


async def test_progress_streams_via_edit_message_on_telegram(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_telegram,
    message_store,
    monkeypatch,
):
    """Tool activity streams as an edited Telegram message (editMessageText);
    the placeholder is cleared before the final answer is sent as a new one."""
    from app.modules.agent_surfaces.services import progress_observer as _po

    monkeypatch.setattr(_po, "_MIN_TEXT_PROGRESS_INTERVAL_SECONDS", 0.0)
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram")
    monkeypatch.setattr(surface_settings, "telegram_webhook_secret", "native-secret")
    monkeypatch.setattr(surface_settings, "enable_telegram_polling_mode", True)
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.telegram.client._TELEGRAM_API_BASE",
        f"{fake_telegram.api_base}/bot",
    )
    pod_id = test_pod["id"]
    sender_id = 555070809
    await _create_agent_surface(authenticated_client, pod_id, config={"type": "TELEGRAM"})
    await _seed_external_user(
        db_session,
        platform="TELEGRAM",
        external_user_id=str(sender_id),
        resolved_user_id=UUID(fixed_test_user["id"]),
    )

    payload = _telegram_payload(text="do some work", message_id=941, sender_id=sender_id)
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="telegram", payload=payload, headers={}),
        script=script_progress(
            ["Searching the web", "Reading the results"],
            final_text="Here is the answer.",
            tool_name="telegram_get_current_chat",
        ),
    )

    edits = await wait_for_messages(message_store, "TELEGRAM_EDIT", min_count=1)
    assert any("Reading the results" in json.dumps(e) for e in edits)
    final = await wait_for_messages(message_store, "TELEGRAM", min_count=1)
    # Telegram renders MarkdownV2, which escapes the trailing "." — match the
    # unescaped portion of the reply text only.
    assert "Here is the answer" in final[-1]["text"]


async def test_progress_streams_via_put_activity_on_teams(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_teams,
    message_store,
    monkeypatch,
):
    """Tool activity streams as a PUT-edited Teams activity; the final answer
    is a new activity POST."""
    from app.core.config import settings as app_settings
    from app.modules.agent_surfaces.platforms.teams.adapter import TeamsSurfaceAdapter
    from app.modules.agent_surfaces.services import progress_observer as _po

    monkeypatch.setattr(_po, "_MIN_TEXT_PROGRESS_INTERVAL_SECONDS", 0.0)

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
    )

    payload = _load_teams_channel_mention_fixture(fake_teams)
    await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="teams", payload=payload, headers={}),
        script=script_progress(
            ["Searching the web", "Reading the results"],
            final_text="Here is the answer.",
            tool_name="teams_get_recent_channel_messages",
        ),
    )

    updates = await wait_for_messages(message_store, "TEAMS_UPDATE", min_count=1)
    assert any("Reading the results" in json.dumps(u) for u in updates)
    final = await wait_for_messages(message_store, "TEAMS", min_count=1)
    final_bodies = [m["body"] for m in final if m.get("body", {}).get("type") == "message"]
    assert "Here is the answer." in final_bodies[-1].get("text", "")
