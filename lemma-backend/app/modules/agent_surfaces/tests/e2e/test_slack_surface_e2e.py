from __future__ import annotations

from app.modules.agent_surfaces.config import surface_settings
import json
from uuid import UUID

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent_surfaces.domain.ingress_context import SurfaceChatContext
from app.modules.agent_surfaces.domain.ingress_request import SurfacePlatformWebhookIngress
from app.modules.agent_surfaces.tests.e2e.helpers import (
    _conversation_by_external_thread,
    _create_agent,
    _create_agent_surface,
    _ensure_connector_account,
    _load_slack_dm_fixture,
    _messages_for_conversation,
)
from app.modules.agent_surfaces.tests.e2e.mock_infrastructure import (
    build_slack_signature_headers,
    wait_for_messages,
)
from app.modules.agent_surfaces.tests.e2e.scripted_llm import (
    process_ingress_and_run_scripted,
    script_text,
)

pytestmark = pytest.mark.e2e


def _slack_channel_payload(*, text: str, channel_id: str, ts: str) -> dict:
    payload = _load_slack_dm_fixture(text=f"<@U0AGSSTQZLH> {text}", ts=ts)
    event = payload["event"]
    event["type"] = "app_mention"
    event["channel"] = channel_id
    event["channel_type"] = "channel"
    event.pop("assistant_thread", None)
    return payload


async def test_slack_identity_policy_blocks_then_allows_sender_domain(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    """A surface restricted to another email domain ignores the sender; widening
    the allow-list to the sender's domain lets the chat through."""
    from app.core.config import settings as app_settings
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
    from app.modules.agent_surfaces.events.handlers import (
        build_surface_event_handler,
    )

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")
    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-identity-policy",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    _, surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    restricted = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={"config": {"identity": {"allowed_domains": ["blocked.example"]}}},
    )
    assert restricted.status_code == 200, restricted.text

    blocked_payload = _load_slack_dm_fixture(
        text="Should be rejected by identity policy",
        ts="1700000000.300300",
    )
    uow = SqlAlchemyUnitOfWork(db_session)
    handler = build_surface_event_handler(uow)
    blocked_context = await handler.prepare_ingress(
        SurfacePlatformWebhookIngress(
            source="slack", payload=blocked_payload, headers={}
        )
    )
    assert blocked_context is None

    sender_domain = fixed_test_user["email"].rsplit("@", 1)[-1]
    allowed = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={"config": {"identity": {"allowed_domains": [sender_domain]}}},
    )
    assert allowed.status_code == 200, allowed.text

    allowed_payload = _load_slack_dm_fixture(
        text="Allowed after widening the domain policy",
        ts="1700000000.300301",
    )
    allowed_context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack", payload=allowed_payload, headers={}
        ),
        script=[script_text("E2E agent reply [SLACK]")],
    )
    assert isinstance(allowed_context, SurfaceChatContext)
    assert allowed_context.surface_id == UUID(surface["id"])

    slack_messages = await wait_for_messages(message_store, "SLACK", min_count=1)
    assert "E2E agent reply [SLACK]" in slack_messages[-1]["text"]


async def test_slack_dm_and_channel_surfaces_route_through_shared_webhook(
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
            "access_token": "xoxb-slack-e2e",
            "scope": "assistant:write,chat:write.customize",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "bot_user_id": "U0AGSSTQZLH",
                "team_id": "T0123456",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    dm_agent, dm_surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(account.id)},
    )
    channel_agent = await _create_agent(
        authenticated_client,
        pod_id,
    )
    route_update = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={
            "config": {
                "channels": [
                    {
                        "channel_id": "C-SUPPORT",
                        "agent_name": channel_agent["name"],
                    }
                ]
            }
        },
    )
    assert route_update.status_code == 200, route_update.text

    dm_payload = _load_slack_dm_fixture(
        text="Hello from Slack DM",
        ts="1700000000.100100",
    )
    raw_body = json.dumps(dm_payload).encode("utf-8")
    response = await authenticated_client.post(
        "/surfaces/webhooks/slack",
        content=raw_body,
        headers=build_slack_signature_headers(
            raw_body=raw_body,
            signing_secret="slack-secret",
        ),
    )
    assert response.status_code == 200, response.text

    dm_context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(source="slack", payload=dm_payload, headers={}),
        script=[script_text("E2E agent reply [SLACK]")],
    )
    assert isinstance(dm_context, SurfaceChatContext)
    assert dm_context.surface_id == UUID(dm_surface["id"])

    channel_payload = _slack_channel_payload(
        text="Need help in channel",
        channel_id="C-SUPPORT",
        ts="1700000000.200200",
    )
    channel_context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack",
            payload=channel_payload,
            headers={},
        ),
        script=[script_text("E2E agent reply [SLACK]")],
    )
    assert isinstance(channel_context, SurfaceChatContext)
    assert channel_context.surface_id == UUID(dm_surface["id"])

    dm_conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=dm_agent["name"],
        external_thread_id="1700000000.100100",
    )
    assert dm_conversation is not None
    channel_conversation = await _conversation_by_external_thread(
        authenticated_client,
        pod_id=pod_id,
        agent_name=channel_agent["name"],
        external_thread_id="1700000000.200200",
    )
    assert channel_conversation is not None

    channel_messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id,
        conversation_id=channel_conversation["id"],
    )
    assert "E2E agent reply [SLACK]" in channel_messages[-1]["text"]

    slack_messages = await wait_for_messages(message_store, "SLACK", min_count=2)
    assert slack_messages[-2]["channel"] == "D0123456"
    assert slack_messages[-1]["channel"] == "C-SUPPORT"
    assert "E2E agent reply [SLACK]" in slack_messages[-1]["text"]


async def test_slack_channel_mention_injects_recent_thread_context(
    authenticated_client: AsyncClient,
    db_session: AsyncSession,
    test_pod,
    fixed_test_user,
    fake_slack,
    message_store,
    monkeypatch,
):
    """A Slack channel mention fetches the recent thread messages and hands them
    to the agent as background context (continuity in a shared thread)."""
    from slack_sdk.web.async_client import AsyncWebClient

    from app.core.config import settings as app_settings

    monkeypatch.setattr(app_settings, "api_url", "https://api.example.test")

    async def fake_replies(self, *, channel, ts, limit, **kwargs):
        return {
            "ok": True,
            "messages": [
                {
                    "user": "U-ALICE",
                    "text": "Can someone summarize the incident?",
                    "ts": "1700000000.100100",
                },
                {
                    "user": "U-BOB",
                    "text": "It started around 2pm after the deploy.",
                    "ts": "1700000000.150150",
                },
            ],
        }

    monkeypatch.setattr(AsyncWebClient, "conversations_replies", fake_replies)

    pod_id = test_pod["id"]
    account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-ctx-e2e",
            "scope": "chat:write,channels:history",
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
    route_update = await authenticated_client.patch(
        f"/pods/{pod_id}/surfaces/slack",
        json={"config": {"channels": [{"channel_id": "C-SUPPORT"}]}},
    )
    assert route_update.status_code == 200, route_update.text

    channel_payload = _slack_channel_payload(
        text="what happened during the incident?",
        channel_id="C-SUPPORT",
        ts="1700000000.300300",
    )
    pod_id_str = pod_id
    context = await process_ingress_and_run_scripted(
        db_session,
        SurfacePlatformWebhookIngress(
            source="slack", payload=channel_payload, headers={}
        ),
        script=[script_text("noted")],
    )
    assert isinstance(context, SurfaceChatContext)

    messages = await _messages_for_conversation(
        authenticated_client,
        pod_id=pod_id_str,
        conversation_id=str(context.conversation_id),
    )
    user_message = next(m for m in messages if m.get("role") == "user")
    channel_context = (user_message.get("metadata") or {}).get("channel_context")
    assert channel_context, channel_context
    assert any("incident" in (m.get("text") or "") for m in channel_context)
    assert any("2pm after the deploy" in (m.get("text") or "") for m in channel_context)
