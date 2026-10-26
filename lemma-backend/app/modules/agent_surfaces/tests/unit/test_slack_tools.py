from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest
from slack_sdk.web.async_client import AsyncWebClient

from app.modules.agent.domain.context import ToolContext
from app.modules.agent_surfaces.platforms.slack.tools import build_slack_surface_toolset


def test_slack_toolset_generates_tool_descriptions():
    toolset = build_slack_surface_toolset(credentials={})

    recent_messages_tool = toolset.tools["slack_get_recent_channel_messages"]
    search_tool = toolset.tools["slack_search_current_channel"]

    assert recent_messages_tool.description
    assert recent_messages_tool.function_schema.description
    assert search_tool.description
    assert search_tool.function_schema.description
    # File upload/download tools were removed; files flow via auto-ingest +
    # display_resource now.
    assert "slack_upload_file" not in toolset.tools
    assert "slack_download_file" not in toolset.tools


@pytest.mark.asyncio
async def test_slack_list_channels_paginates(monkeypatch):
    from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService

    pages = [
        {
            "channels": [{"id": "C1", "name": "general", "is_member": True}],
            "response_metadata": {"next_cursor": "cur2"},
        },
        {
            "channels": [{"id": "C2", "name": "random", "is_member": False}],
            "response_metadata": {"next_cursor": ""},
        },
    ]
    seen_cursors: list = []

    async def fake_list(self, **kwargs):
        seen_cursors.append(kwargs.get("cursor"))
        return pages[len(seen_cursors) - 1]

    monkeypatch.setattr(AsyncWebClient, "conversations_list", fake_list)

    channels = await SlackPlatformService(
        credentials={"access_token": "xoxb-test"}
    ).list_channels()

    assert [c.id for c in channels] == ["C1", "C2"]
    assert channels[0].name == "general"
    assert channels[0].is_member is True
    assert seen_cursors == [None, "cur2"]


@pytest.mark.asyncio
async def test_slack_recent_channel_messages_excludes_active_thread(monkeypatch):
    toolset = build_slack_surface_toolset(credentials={"access_token": "xoxb-test"})
    tool = toolset.tools["slack_get_recent_channel_messages"]

    async def fake_history(self, **kwargs):
        assert kwargs["channel"] == "C123"
        assert kwargs["latest"] == "1700.1"
        assert kwargs["inclusive"] is False
        return {
            "messages": [
                {"ts": "1699.9", "user": "U2", "text": "Earlier channel message"},
                {
                    "ts": "1699.8",
                    "user": "U3",
                    "text": "Thread duplicate",
                    "thread_ts": "1700.1",
                },
            ]
        }

    monkeypatch.setattr(AsyncWebClient, "conversations_history", fake_history)

    ctx = SimpleNamespace(
        deps=ToolContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            surface_platform="SLACK",
            external_channel_id="C123",
            external_thread_id="1700.1",
        )
    )
    request = SimpleNamespace(limit=10, include_current_thread=False)

    response = await tool.function(ctx, request)

    assert response.success is True
    assert [item.text for item in response.messages] == ["Earlier channel message"]
