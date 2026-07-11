"""Public conversation journeys for platform-specific surface tools."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from app.core.infrastructure.cache.redis_json_cache import RedisJsonCache
from app.modules.agent_surfaces.tests.e2e.helpers import (
    REAL_TEAMS_CHANNEL_ID,
    REAL_TEAMS_TENANT_ID,
    _create_agent_surface,
    _ensure_connector_account,
)
from app.modules.test_support.e2e.scripted_model import (
    script_text,
    script_tool_call,
)

pytestmark = pytest.mark.e2e


async def _run_public_surface_tool_script(
    client,
    *,
    pod_id: str,
    agent_name: str,
    metadata: dict[str, Any],
    script: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    conversation = await client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "metadata": {
                **metadata,
                "mock_llm_script": [*script, script_text("Surface tools complete.")],
                "source": "surface-tool-worker-e2e",
            },
        },
    )
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["id"]

    terminal: dict[str, Any] | None = None
    async with client.stream(
        "POST",
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        json={"content": "Inspect the current platform context."},
        timeout=60,
    ) as response:
        assert response.status_code == 200, await response.aread()
        async with asyncio.timeout(45):
            async for line in response.aiter_lines():
                if not line.startswith("data: "):
                    continue
                event = json.loads(line.removeprefix("data: "))
                if event.get("type") in {"completed", "stopped", "error"}:
                    terminal = event
                    break
    assert terminal is not None
    assert terminal["type"] == "completed", terminal

    messages = await client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == 200, messages.text
    items = messages.json()["items"]
    assert any(
        item["role"] == "assistant" and item["text"] == "Surface tools complete."
        for item in items
    )
    return items


def _tool_result(items: list[dict[str, Any]], tool_name: str) -> dict[str, Any]:
    result = next(
        item
        for item in items
        if item.get("kind") == "TOOL_RETURN" and item.get("tool_name") == tool_name
    )
    payload = result["tool_result"]
    assert isinstance(payload, dict), payload
    return payload


def _tool_result_by_id(
    items: list[dict[str, Any]], tool_call_id: str
) -> dict[str, Any]:
    result = next(
        item
        for item in items
        if item.get("kind") == "TOOL_RETURN"
        and item.get("tool_call_id") == tool_call_id
    )
    payload = result["tool_result"]
    assert isinstance(payload, dict), payload
    return payload


@pytest.mark.asyncio
async def test_public_agent_runs_platform_context_tools_through_real_worker(
    authenticated_client,
    db_session,
    e2e_settings,
    test_pod,
    fixed_test_user,
    fake_slack,
    fake_teams,
    fake_telegram,
    worker,
):
    """A user can run Slack/Teams/Telegram/WhatsApp context tools through
    HTTP+SSE; each call reaches the production worker, platform adapter, and
    provider contract and persists its structured tool return."""

    del worker
    pod_id = test_pod["id"]

    slack_account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="slack",
        credentials={
            "access_token": "xoxb-surface-tools",
            "api_base_url": fake_slack.base_url,
            "raw_response": {
                "team_id": "T-SURFACE-TOOLS",
                "bot_user_id": "U-SURFACE-TOOLS",
                "api_base_url": fake_slack.base_url,
            },
        },
    )
    slack_agent, slack_surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "SLACK", "account_id": str(slack_account.id)},
    )
    slack_items = await _run_public_surface_tool_script(
        authenticated_client,
        pod_id=pod_id,
        agent_name=slack_agent["name"],
        metadata={
            "surface_id": slack_surface["id"],
            "surface_platform": "SLACK",
            "surface_event_metadata": {
                "platform": "SLACK",
                "is_thread_reply": True,
            },
            "external_channel_id": "C-SUPPORT",
            "external_thread_id": "1700000000.777002",
            "external_user_id": "U-CURRENT",
        },
        script=[
            script_tool_call(
                "slack_get_recent_channel_messages",
                {"request": {"limit": 10, "include_current_thread": False}},
                tool_call_id="slack-recent",
            ),
            script_tool_call(
                "slack_search_current_channel",
                {
                    "request": {
                        "query": "support",
                        "limit": 5,
                        "scan_limit": 20,
                        "include_current_thread": False,
                    }
                },
                tool_call_id="slack-search",
            ),
            script_tool_call(
                "slack_search_current_channel",
                {
                    "request": {
                        "query": " ",
                        "limit": 5,
                        "scan_limit": 10,
                    }
                },
                tool_call_id="slack-empty-search",
            ),
        ],
    )
    slack_recent = _tool_result(slack_items, "slack_get_recent_channel_messages")
    assert slack_recent["success"] is True
    assert any(
        item["text"].startswith("Earlier support context")
        for item in slack_recent["messages"]
    )
    support_context = next(
        item
        for item in slack_recent["messages"]
        if item["text"].startswith("Earlier support context")
    )
    assert support_context["attachments"][0]["name"] == "support-context.txt"
    slack_search = _tool_result(slack_items, "slack_search_current_channel")
    assert slack_search["success"] is True
    assert len(slack_search["matches"]) == 1
    assert slack_search["matches"][0]["text"].startswith("Earlier support context")
    slack_empty_search = _tool_result_by_id(slack_items, "slack-empty-search")
    assert slack_empty_search["success"] is False
    assert slack_empty_search["error"] == "Query cannot be empty."

    slack_missing_context_items = await _run_public_surface_tool_script(
        authenticated_client,
        pod_id=pod_id,
        agent_name=slack_agent["name"],
        metadata={
            "surface_id": slack_surface["id"],
            "surface_platform": "SLACK",
        },
        script=[
            script_tool_call(
                "slack_get_recent_channel_messages",
                {"request": {"limit": 10}},
                tool_call_id="slack-missing-context",
            )
        ],
    )
    slack_missing_context = _tool_result_by_id(
        slack_missing_context_items, "slack-missing-context"
    )
    assert slack_missing_context["success"] is False
    assert "missing channel credentials" in slack_missing_context["error"]

    teams_account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="microsoft_teams",
        credentials={
            "access_token": "teams-surface-tools",
            "graph_api_base_url": fake_teams.graph_base_url,
            "user_data": {"tenant_id": REAL_TEAMS_TENANT_ID},
        },
    )
    teams_agent, teams_surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={
            "type": "TEAMS",
            "account_id": str(teams_account.id),
            "allowed_channel_ids": [REAL_TEAMS_CHANNEL_ID],
        },
    )
    token_cache = RedisJsonCache(
        e2e_settings.redis_url,
        key_prefix="surface:teams-token",
        ttl_seconds=3600,
    )
    await token_cache.set_raw(
        f"{REAL_TEAMS_TENANT_ID}:https://graph.microsoft.com/.default",
        "teams-graph-token",
    )
    await token_cache.close()
    teams_conversation_metadata = {
        "surface_id": teams_surface["id"],
        "surface_platform": "TEAMS",
        "surface_event_metadata": {
            "platform": "TEAMS",
            "team_id": ("19:OHV_1hj7zqTYZp07gcOvjWBdiwR8UML4cj3vny7OANk1@thread.tacv2"),
            "team_aad_group_id": "27029c82-5e8f-48a5-ae72-4cb914987d30",
            "channel_id": REAL_TEAMS_CHANNEL_ID,
            "service_url": fake_teams.service_url,
            "conversation_id": "teams-surface-tool-thread",
        },
        "external_channel_id": REAL_TEAMS_CHANNEL_ID,
        "external_thread_id": "1776236638028",
        "external_user_id": "teams-surface-tool-user",
    }
    teams_items = await _run_public_surface_tool_script(
        authenticated_client,
        pod_id=pod_id,
        agent_name=teams_agent["name"],
        metadata=teams_conversation_metadata,
        script=[
            script_tool_call(
                "teams_get_recent_channel_messages",
                {"request": {"limit": 10, "scope": "thread"}},
                tool_call_id="teams-thread",
            ),
            script_tool_call(
                "teams_get_recent_channel_messages",
                {"request": {"limit": 10, "scope": "channel"}},
                tool_call_id="teams-channel",
            ),
            script_tool_call(
                "teams_get_recent_channel_messages",
                {"request": {"limit": 10, "scope": "auto"}},
                tool_call_id="teams-auto",
            ),
        ],
    )
    teams_results = [
        item["tool_result"]
        for item in teams_items
        if item.get("kind") == "TOOL_RETURN"
        and item.get("tool_name") == "teams_get_recent_channel_messages"
    ]
    assert len(teams_results) == 3
    assert all(result["success"] is True for result in teams_results)
    assert any(
        message["text"] == "Earlier customer context"
        for result in teams_results
        for message in result["messages"]
    )
    assert any(
        attachment["name"] == "customer-context.pdf"
        for result in teams_results
        for message in result["messages"]
        for attachment in message["attachments"]
    )

    teams_no_thread_items = await _run_public_surface_tool_script(
        authenticated_client,
        pod_id=pod_id,
        agent_name=teams_agent["name"],
        metadata={
            **teams_conversation_metadata,
            "external_thread_id": REAL_TEAMS_CHANNEL_ID,
        },
        script=[
            script_tool_call(
                "teams_get_recent_channel_messages",
                {"request": {"limit": 10, "scope": "thread"}},
                tool_call_id="teams-no-thread",
            )
        ],
    )
    teams_no_thread = _tool_result_by_id(teams_no_thread_items, "teams-no-thread")
    assert teams_no_thread["success"] is False
    assert "no current Teams thread" in teams_no_thread["error"]

    teams_missing_channel_items = await _run_public_surface_tool_script(
        authenticated_client,
        pod_id=pod_id,
        agent_name=teams_agent["name"],
        metadata={
            key: value
            for key, value in teams_conversation_metadata.items()
            if key != "external_channel_id"
        },
        script=[
            script_tool_call(
                "teams_get_recent_channel_messages",
                {"request": {"limit": 10, "scope": "channel"}},
                tool_call_id="teams-missing-channel",
            )
        ],
    )
    teams_missing_channel = _tool_result_by_id(
        teams_missing_channel_items, "teams-missing-channel"
    )
    assert teams_missing_channel["success"] is False
    assert "team channel conversations" in teams_missing_channel["error"]

    fake_teams.graph_failure_status = 503
    teams_provider_failure_items = await _run_public_surface_tool_script(
        authenticated_client,
        pod_id=pod_id,
        agent_name=teams_agent["name"],
        metadata=teams_conversation_metadata,
        script=[
            script_tool_call(
                "teams_get_recent_channel_messages",
                {"request": {"limit": 10, "scope": "channel"}},
                tool_call_id="teams-provider-failure",
            )
        ],
    )
    teams_provider_failure = _tool_result_by_id(
        teams_provider_failure_items, "teams-provider-failure"
    )
    assert teams_provider_failure["success"] is False
    assert teams_provider_failure["error"] == "Graph API returned HTTP 503."
    assert teams_provider_failure["messages"] == []

    telegram_account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="telegram",
        credentials={
            "bot_token": "telegram-surface-tools",
            "api_base_url": f"{fake_telegram.api_base}/bot",
        },
    )
    telegram_agent, telegram_surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "TELEGRAM", "account_id": str(telegram_account.id)},
    )
    telegram_items = await _run_public_surface_tool_script(
        authenticated_client,
        pod_id=pod_id,
        agent_name=telegram_agent["name"],
        metadata={
            "surface_id": telegram_surface["id"],
            "surface_platform": "TELEGRAM",
            "surface_event_metadata": {
                "platform": "TELEGRAM",
                "chat_type": "supergroup",
                "chat_id": "-100123456",
                "is_topic_message": True,
                "message_thread_id": "42",
            },
            "external_channel_id": "-100123456",
            "external_thread_id": "42",
            "external_user_id": "telegram-surface-tool-user",
        },
        script=[
            script_tool_call(
                "telegram_get_current_chat",
                {"request": {}},
                tool_call_id="telegram-current",
            )
        ],
    )
    telegram_result = _tool_result(telegram_items, "telegram_get_current_chat")
    assert telegram_result["success"] is True
    assert telegram_result["chat_id"] == "-100123456"
    assert telegram_result["message_thread_id"] == "42"

    whatsapp_account = await _ensure_connector_account(
        db_session,
        user_id=fixed_test_user["id"],
        connector_id="whatsapp",
        credentials={
            "access_token": "whatsapp-surface-tools",
            "phone_number_id": "1234567890",
            "waba_id": "waba-surface-tools",
        },
    )
    whatsapp_agent, whatsapp_surface = await _create_agent_surface(
        authenticated_client,
        pod_id,
        config={"type": "WHATSAPP", "account_id": str(whatsapp_account.id)},
    )
    whatsapp_items = await _run_public_surface_tool_script(
        authenticated_client,
        pod_id=pod_id,
        agent_name=whatsapp_agent["name"],
        metadata={
            "surface_id": whatsapp_surface["id"],
            "surface_platform": "WHATSAPP",
            "surface_event_metadata": {
                "platform": "WHATSAPP",
                "waba_id": "waba-surface-tools",
                "phone_number_id": "1234567890",
                "contacts": [{"wa_id": "15550550123", "profile": {"name": "Ada"}}],
            },
            "external_channel_id": "1234567890",
            "external_thread_id": "15550550123@1234567890",
            "external_user_id": "15550550123",
        },
        script=[
            script_tool_call(
                "whatsapp_get_current_contact",
                {"request": {}},
                tool_call_id="whatsapp-current",
            )
        ],
    )
    whatsapp_result = _tool_result(whatsapp_items, "whatsapp_get_current_contact")
    assert whatsapp_result["success"] is True
    assert whatsapp_result["phone_number_id"] == "1234567890"
    assert whatsapp_result["wa_id"] == "15550550123"
    assert whatsapp_result["display_name"] == "Ada"
