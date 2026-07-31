from __future__ import annotations

import json
from dataclasses import dataclass
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent.tools.context import ConversationContext
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    TeamsSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms.teams.tools import (
    build_teams_surface_toolset,
)


@dataclass
class _PlannedResponse:
    method: str
    matcher: str
    status: int
    json_data: dict | None = None
    text_data: str | None = None
    body: bytes = b""


class _FakeResponse:
    def __init__(
        self,
        *,
        status: int,
        json_data: dict | None = None,
        text_data: str | None = None,
        body: bytes = b"",
    ) -> None:
        self.status = status
        self._json_data = json_data
        self._text_data = text_data
        self._body = body

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def json(self, content_type=None):
        return self._json_data

    async def text(self):
        if self._text_data is not None:
            return self._text_data
        if self._json_data is not None:
            return json.dumps(self._json_data)
        return self._body.decode("utf-8", errors="replace")

    async def read(self):
        return self._body


class _FakeSession:
    def __init__(self, planned: list[_PlannedResponse], calls: list[dict]) -> None:
        self._planned = planned
        self._calls = calls

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def get(self, url: str, **kwargs):
        return self._request("GET", url, kwargs)

    def post(self, url: str, **kwargs):
        return self._request("POST", url, kwargs)

    def put(self, url: str, **kwargs):
        return self._request("PUT", url, kwargs)

    def _request(self, method: str, url: str, kwargs: dict):
        self._calls.append(
            {
                "method": method,
                "url": url,
                "headers": kwargs.get("headers") or {},
                "json": kwargs.get("json"),
                "data": kwargs.get("data"),
            }
        )
        for index, planned in enumerate(self._planned):
            if planned.method == method and planned.matcher in url:
                match = self._planned.pop(index)
                return _FakeResponse(
                    status=match.status,
                    json_data=match.json_data,
                    text_data=match.text_data,
                    body=match.body,
                )
        raise AssertionError(f"Unexpected {method} request to {url}")


def _session_factory(planned: list[_PlannedResponse], calls: list[dict]):
    def _factory():
        return _FakeSession(planned, calls)

    return _factory


def test_teams_toolset_generates_tool_descriptions():
    toolset = build_teams_surface_toolset(credentials={})

    recent_messages_tool = toolset.tools["teams_get_recent_channel_messages"]

    assert recent_messages_tool.description
    assert recent_messages_tool.function_schema.description
    # File send/download tools were removed; files flow via auto-ingest +
    # display_resource now.
    assert "teams_send_file" not in toolset.tools
    assert "teams_download_file" not in toolset.tools


@pytest.mark.asyncio
async def test_teams_get_recent_channel_messages_resolves_graph_team_id(
    monkeypatch,
):
    toolset = build_teams_surface_toolset(
        credentials={"user_data": {"tenant_id": "tenant-123"}, "raw_response": {}}
    )
    tool = toolset.tools["teams_get_recent_channel_messages"]
    calls: list[dict] = []
    planned = [
        _PlannedResponse(
            "GET",
            "/v3/teams/19%3Ateam%40thread.tacv2",
            200,
            json_data={"id": "19:team@thread.tacv2", "aadGroupId": "11111111-2222-4333-8444-555555555555"},
        ),
        _PlannedResponse(
            "GET",
            "/teams/11111111-2222-4333-8444-555555555555/channels/19%3Achannel%40thread.tacv2/messages",
            200,
            json_data={
                "value": [
                    {
                        "id": "old-1",
                        "body": {"content": "<div>Earlier message</div>"},
                        "from": {"user": {"id": "user-1", "displayName": "Teammate"}},
                    },
                    {
                        "id": "thread-1",
                        "body": {"content": "<div>Trigger message</div>"},
                        "from": {"user": {"id": "user-2", "displayName": "Trigger"}},
                    },
                ]
            },
        ),
    ]

    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.teams.client.get_graph_token",
        AsyncMock(return_value="graph-token"),
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.teams.client.get_bot_token",
        AsyncMock(return_value="bot-token"),
    )
    monkeypatch.setattr(
        "aiohttp.ClientSession",
        _session_factory(planned, calls),
    )

    ctx = SimpleNamespace(
        deps=ConversationContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            surface_platform="TEAMS",
            external_channel_id="19:channel@thread.tacv2",
            external_thread_id="thread-1",
            surface_metadata=TeamsSurfaceEventMetadata(
                team_id="19:team@thread.tacv2",
                service_url="https://smba.example.test/teams",
            ),
        )
    )
    request = SimpleNamespace(limit=10, scope="channel")

    response = await tool.function(ctx, request)

    assert response.success is True
    assert [message.text for message in response.messages] == ["Earlier message"]
    assert any(
        call["method"] == "GET"
        and call["url"].endswith("/v3/teams/19%3Ateam%40thread.tacv2")
        for call in calls
    )


@pytest.mark.asyncio
async def test_teams_fetch_recent_context_maps_graph_messages(monkeypatch):
    """The ingress-side fetch_recent_context derives Graph params from the event
    and maps channel messages to background SurfaceContextMessage entries."""
    from app.modules.agent_surfaces.domain.entities import (
        ConversationType,
        ParsedInboundSurfaceEvent,
    )
    from app.modules.agent_surfaces.platforms.teams.service import (
        TeamsPlatformService,
    )

    calls: list[dict] = []
    planned = [
        _PlannedResponse(
            "GET",
            "/v3/teams/19%3Ateam%40thread.tacv2",
            200,
            json_data={
                "id": "19:team@thread.tacv2",
                "aadGroupId": "11111111-2222-4333-8444-555555555555",
            },
        ),
        _PlannedResponse(
            "GET",
            "/teams/11111111-2222-4333-8444-555555555555/channels/19%3Achannel%40thread.tacv2/messages",
            200,
            json_data={
                "value": [
                    {
                        "id": "m1",
                        "body": {"content": "<div>First message</div>"},
                        "from": {"user": {"id": "u1", "displayName": "Alice"}},
                    },
                    {
                        "id": "m2",
                        "body": {"content": "<div>Second message</div>"},
                        "from": {"user": {"id": "u2", "displayName": "Bob"}},
                    },
                ]
            },
        ),
    ]
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.teams.client.get_graph_token",
        AsyncMock(return_value="graph-token"),
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.teams.client.get_bot_token",
        AsyncMock(return_value="bot-token"),
    )
    monkeypatch.setattr("aiohttp.ClientSession", _session_factory(planned, calls))

    service = TeamsPlatformService(
        credentials={"user_data": {"tenant_id": "tenant-123"}, "raw_response": {}}
    )
    event = ParsedInboundSurfaceEvent(
        platform="TEAMS",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        external_channel_id="19:channel@thread.tacv2",
        external_thread_id="19:channel@thread.tacv2",
        external_message_id="trigger-1",
        message_text="@lemma help",
        metadata={
            "team_id": "19:team@thread.tacv2",
            "service_url": "https://smba.example.test/teams",
        },
    )

    messages = await service.fetch_recent_context(event=event, limit=10)
    texts = {m.text for m in messages}
    assert "First message" in texts and "Second message" in texts
    assert all(m.author for m in messages)


@pytest.mark.asyncio
async def test_teams_get_recent_thread_messages_include_files(
    monkeypatch,
):
    toolset = build_teams_surface_toolset(
        credentials={"user_data": {"tenant_id": "tenant-123"}, "raw_response": {}}
    )
    tool = toolset.tools["teams_get_recent_channel_messages"]
    calls: list[dict] = []
    planned = [
        _PlannedResponse(
            "GET",
            "/v3/teams/19%3Ateam%40thread.tacv2",
            200,
            json_data={"id": "19:team@thread.tacv2", "aadGroupId": "11111111-2222-4333-8444-555555555555"},
        ),
        _PlannedResponse(
            "GET",
            "/teams/11111111-2222-4333-8444-555555555555/channels/19%3Achannel%40thread.tacv2/messages/thread-1/replies",
            200,
            json_data={
                "value": [
                    {
                        "id": "reply-1",
                        "replyToId": "thread-1",
                        "body": {"content": ""},
                        "from": {"user": {"id": "user-1", "displayName": "Teammate"}},
                        "attachments": [
                            {
                                "name": "diagram.png",
                                "contentType": "image/png",
                                "contentUrl": "https://files.example/diagram.png",
                            }
                        ],
                    }
                ]
            },
        ),
    ]

    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.teams.client.get_graph_token",
        AsyncMock(return_value="graph-token"),
    )
    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.teams.client.get_bot_token",
        AsyncMock(return_value="bot-token"),
    )
    monkeypatch.setattr(
        "aiohttp.ClientSession",
        _session_factory(planned, calls),
    )

    ctx = SimpleNamespace(
        deps=ConversationContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            surface_platform="TEAMS",
            external_channel_id="19:channel@thread.tacv2",
            external_thread_id="thread-1",
            surface_metadata=TeamsSurfaceEventMetadata(
                team_id="19:team@thread.tacv2",
                service_url="https://smba.example.test/teams",
            ),
        )
    )
    request = SimpleNamespace(limit=10, scope="thread")

    response = await tool.function(ctx, request)

    assert response.success is True
    assert [message.text for message in response.messages] == ["[File shared: diagram.png]"]
    assert response.messages[0].attachments[0].download_url == "https://files.example/diagram.png"
    assert response.messages[0].attachments[0].content_type == "image/png"


def test_teams_download_helper_classifies_urls_and_credentials():
    from app.modules.agent_surfaces.platforms.teams.service import (
        _is_raw_sharepoint_document_url,
        _looks_like_bot_attachment_url,
        _split_sharepoint_site_and_item_path,
        _tenant_id_from_credentials,
    )

    assert (
        _tenant_id_from_credentials({"user_data": {"tenant_id": "tenant-user"}})
        == "tenant-user"
    )
    assert (
        _tenant_id_from_credentials({"raw_response": {"tid": "tenant-raw"}})
        == "tenant-raw"
    )
    assert _looks_like_bot_attachment_url(
        "https://smba.trafficmanager.net/amer/v3/attachments/abc/views/original"
    )
    assert _is_raw_sharepoint_document_url(
        "https://example.sharepoint.com/sites/Eng/Shared%20Documents/report.docx"
    )
    assert _split_sharepoint_site_and_item_path(
        "/sites/Eng/Shared Documents/report.docx"
    ) == ("/sites/Eng", "/Shared Documents/report.docx")


@pytest.mark.asyncio
async def test_teams_download_attachment_returns_none_without_tokens(monkeypatch):
    from app.modules.agent_surfaces.domain.entities import (
        ConversationType,
        ParsedInboundSurfaceEvent,
    )
    from app.modules.agent_surfaces.platforms.teams.service import TeamsPlatformService

    monkeypatch.setattr(
        "app.modules.agent_surfaces.platforms.teams.client.get_bot_token",
        AsyncMock(return_value=None),
    )
    service = TeamsPlatformService(credentials={"tenant_id": "tenant-1"})

    result = await service.download_attachment_bytes(
        ParsedInboundSurfaceEvent(
            platform="TEAMS",
            conversation_type=ConversationType.EXTERNAL_GROUP,
            external_thread_id="thread",
            message_text="file",
        ),
        {
            "download_url": "https://smba.trafficmanager.net/amer/v3/attachments/abc",
            "name": "file.txt",
            "content_type": "text/plain",
        },
    )

    assert result is None
