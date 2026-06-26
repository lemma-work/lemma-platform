from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from app.modules.agent_surfaces.domain.entities import ConversationType
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.models import (
    SurfaceDisplayAction,
    SurfaceDisplayRenderPlan,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.platforms.slack.client import (
    slack_access_token,
)
from app.modules.agent_surfaces.platforms.slack.adapter import (
    SlackSurfaceAdapter,
)
from app.modules.agent_surfaces.platforms.teams.adapter import (
    TeamsSurfaceAdapter,
)
from app.modules.agent_surfaces.platforms.teams import (
    adapter as teams_adapter_module,
)


def test_surface_adapter_registry_accepts_surface_platform_enum():
    registry = SurfacePlatformAdapterRegistry()

    assert isinstance(registry.get(SurfacePlatform.TEAMS), TeamsSurfaceAdapter)


def test_slack_access_token_accepts_user_provided_bot_token():
    assert (
        slack_access_token({"bot_token": "xoxb-user-provided"}) == "xoxb-user-provided"
    )


@pytest.mark.asyncio
async def test_slack_dm_uses_thread_as_external_thread_id():
    adapter = SlackSurfaceAdapter()
    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event": {
            "type": "message",
            "user": "U123",
            "text": "hello",
            "channel": "D123",
            "channel_type": "im",
            "ts": "1700000000.0001",
            "assistant_thread": {"action_token": "action-token"},
        },
    }

    event = await adapter.parse_inbound_event(payload, {})

    assert event is not None
    assert event.conversation_type == ConversationType.EXTERNAL_DM
    assert event.external_thread_id == "1700000000.0001"
    assert event.reply_target["thread_ts"] == "1700000000.0001"


@pytest.mark.asyncio
async def test_slack_channel_message_mention_starts_threaded_conversation():
    adapter = SlackSurfaceAdapter()
    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "authorizations": [{"user_id": "U-BOT"}],
        "event": {
            "type": "message",
            "user": "U123",
            "text": "<@U-BOT> can you help?",
            "channel": "C123",
            "channel_type": "channel",
            "ts": "1700000000.0001",
        },
    }

    event = await adapter.parse_inbound_event(payload, {})

    assert event is not None
    assert event.conversation_type == ConversationType.EXTERNAL_GROUP
    assert event.mentioned_agent is True
    assert event.should_start_conversation is True
    assert event.external_thread_id == "1700000000.0001"
    assert event.reply_target["thread_ts"] == "1700000000.0001"
    assert event.metadata["mentioned_user_ids"] == ["U-BOT"]


@pytest.mark.asyncio
async def test_slack_adapter_unwraps_nested_event_payload():
    adapter = SlackSurfaceAdapter()
    payload = {
        "event_type": "surface.slack.webhook",
        "payload": {
            "type": "event_callback",
            "team_id": "T123",
            "event": {
                "type": "message",
                "user": "U123",
                "text": "hello",
                "channel": "D123",
                "channel_type": "im",
                "ts": "1700000000.0001",
            },
        },
    }

    event = await adapter.parse_inbound_event(payload, {})

    assert event is not None
    assert event.message_text == "hello"
    assert event.external_thread_id == "1700000000.0001"


@pytest.mark.asyncio
async def test_slack_file_share_message_includes_attachment_details():
    adapter = SlackSurfaceAdapter()
    payload = {
        "type": "event_callback",
        "team_id": "T123",
        "event": {
            "type": "message",
            "subtype": "file_share",
            "user": "U123",
            "text": "<@U-BOT> what does this image say?",
            "channel": "C123",
            "channel_type": "channel",
            "ts": "1700000000.0002",
            "files": [
                {
                    "id": "F123",
                    "name": "quote.png",
                    "title": "quote.png",
                    "mimetype": "image/png",
                    "filetype": "png",
                    "size": 2048,
                    "url_private": "https://files.slack.com/files-pri/T1-F123/quote.png",
                    "url_private_download": "https://files.slack.com/files-pri/T1-F123/download/quote.png",
                    "permalink": "https://workspace.slack.com/files/U1/F123/quote.png",
                }
            ],
        },
    }

    event = await adapter.parse_inbound_event(payload, {})

    assert event is not None
    assert "Files attached to this Slack message:" in event.message_text
    assert "quote.png | image/png | 2048 bytes | id=F123" in event.message_text
    assert (
        "download_url=https://files.slack.com/files-pri/T1-F123/download/quote.png"
        in event.message_text
    )
    assert event.metadata["attachments"] == [
        {
            "id": "F123",
            "name": "quote.png",
            "download_url": "https://files.slack.com/files-pri/T1-F123/download/quote.png",
            "permalink": "https://workspace.slack.com/files/U1/F123/quote.png",
            "content_type": "image/png",
            "file_type": "png",
            "mime_type": "image/png",
            "size": 2048,
        }
    ]


@pytest.mark.asyncio
async def test_teams_channel_reply_uses_reply_to_id_as_thread_id():
    adapter = TeamsSurfaceAdapter()
    payload = {
        "type": "message",
        "id": "msg-2",
        "text": "<at>LemmaChat</at> can you help?",
        "serviceUrl": "https://smba.example.test/teams",
        "from": {"id": "29:user", "aadObjectId": "aad-1", "name": "Rahul"},
        "conversation": {"id": "conv-1", "conversationType": "channel"},
        "recipient": {"id": "28:bot", "name": "LemmaChat"},
        "replyToId": "msg-1",
        "channelData": {
            "tenant": {"id": "tenant-1"},
            "team": {"id": "19:team@thread.tacv2"},
            "channel": {"id": "19:channel@thread.tacv2"},
        },
        "entities": [
            {
                "type": "mention",
                "mentioned": {"id": "28:bot", "name": "LemmaChat"},
            }
        ],
    }

    event = await adapter.parse_inbound_event(payload, {})

    assert event is not None
    assert event.conversation_type == ConversationType.EXTERNAL_GROUP
    assert event.external_channel_id == "19:channel@thread.tacv2"
    assert event.external_thread_id == "msg-1"
    assert event.mentioned_agent is True
    assert event.metadata["team_id"] == "19:team@thread.tacv2"
    assert event.metadata["service_url"] == "https://smba.example.test/teams"
    assert event.metadata["conversation_id"] == "conv-1"
    assert event.metadata["reply_to_id"] == "msg-2"


@pytest.mark.asyncio
async def test_teams_dm_uses_conversation_id_as_thread_id():
    adapter = TeamsSurfaceAdapter()
    payload = {
        "type": "message",
        "id": "msg-1",
        "text": "hello from teams dm",
        "serviceUrl": "https://smba.example.test/teams",
        "from": {"id": "29:user", "aadObjectId": "aad-1", "name": "Rahul"},
        "conversation": {"id": "a:dm-conversation", "conversationType": "personal"},
        "recipient": {"id": "28:bot", "name": "LemmaChat"},
        "channelData": {"tenant": {"id": "tenant-1"}},
    }

    event = await adapter.parse_inbound_event(payload, {})

    assert event is not None
    assert event.conversation_type == ConversationType.EXTERNAL_DM
    assert event.external_channel_id == "a:dm-conversation"
    assert event.external_thread_id == "a:dm-conversation"
    assert event.reply_target["conversation_id"] == "a:dm-conversation"
    assert event.metadata["conversation_id"] == "a:dm-conversation"
    assert event.metadata["reply_to_id"] is None


@pytest.mark.asyncio
async def test_teams_send_message_declares_markdown_text_format(monkeypatch):
    posted: dict = {}

    class _Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self):
            return None

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def post(self, url, *, headers, json):
            posted["url"] = url
            posted["headers"] = headers
            posted["json"] = json
            return _Response()

    adapter = TeamsSurfaceAdapter()
    adapter._get_bot_token = AsyncMock(return_value="bot-token")
    monkeypatch.setattr(
        teams_adapter_module.aiohttp,
        "ClientSession",
        lambda *args, **kwargs: _Session(),
    )
    event = ParsedInboundSurfaceEvent(
        platform="TEAMS",
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id="tenant-1",
        external_channel_id="a:dm-conversation",
        external_thread_id="a:dm-conversation",
        external_message_id="msg-1",
        sender_external_user_id="29:user",
        message_text="hello",
        is_dm=True,
        reply_target={
            "service_url": "https://smba.example.test/teams",
            "conversation_id": "a:dm-conversation",
            "reply_to_id": "msg-1",
        },
    )

    await adapter.send_message(
        credentials={},
        event=event,
        message="**bold**\n\n- item",
    )

    assert posted["url"].endswith("/v3/conversations/a%3Adm-conversation/activities")
    assert posted["headers"]["Authorization"] == "Bearer bot-token"
    assert posted["json"] == {
        "type": "message",
        "text": "**bold**\n\n- item",
        "textFormat": "markdown",
        "replyToId": "msg-1",
    }


@pytest.mark.asyncio
async def test_teams_send_display_resource_posts_adaptive_card(monkeypatch):
    posted: dict = {}

    class _Response:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def raise_for_status(self):
            return None

    class _Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

        def post(self, url, *, headers, json):
            posted["url"] = url
            posted["headers"] = headers
            posted["json"] = json
            return _Response()

    adapter = TeamsSurfaceAdapter()
    adapter._get_bot_token = AsyncMock(return_value="bot-token")
    monkeypatch.setattr(
        teams_adapter_module.aiohttp,
        "ClientSession",
        lambda *args, **kwargs: _Session(),
    )
    event = ParsedInboundSurfaceEvent(
        platform="TEAMS",
        conversation_type=ConversationType.EXTERNAL_DM,
        tenant_id="tenant-1",
        external_channel_id="a:dm-conversation",
        external_thread_id="a:dm-conversation",
        external_message_id="msg-1",
        sender_external_user_id="29:user",
        message_text="hello",
        is_dm=True,
        reply_target={
            "service_url": "https://smba.example.test/teams",
            "conversation_id": "a:dm-conversation",
            "reply_to_id": "msg-1",
        },
    )

    await adapter.send_display_resource(
        credentials={},
        event=event,
        render_plan=SurfaceDisplayRenderPlan(
            resource_type="TABLE",
            title="Table: deals",
            summary="A datastore view is ready.",
            detail_lines=["Filters: stage eq won"],
            actions=[
                SurfaceDisplayAction(
                    label="Open in Lemma",
                    url="https://app.example.test/pod/p/data?tab=deals",
                )
            ],
        ),
    )

    attachment = posted["json"]["attachments"][0]
    assert attachment["contentType"] == "application/vnd.microsoft.card.adaptive"
    assert attachment["content"]["type"] == "AdaptiveCard"
    assert attachment["content"]["actions"][0]["type"] == "Action.OpenUrl"
    assert posted["json"]["replyToId"] == "msg-1"


@pytest.mark.asyncio
async def test_teams_adapter_extracts_file_only_message_attachments():
    adapter = TeamsSurfaceAdapter()
    payload = {
        "type": "message",
        "id": "msg-3",
        "serviceUrl": "https://smba.example.test/teams",
        "from": {"id": "29:user", "aadObjectId": "aad-1", "name": "Rahul"},
        "conversation": {"id": "a:dm-conversation", "conversationType": "personal"},
        "recipient": {"id": "28:bot", "name": "LemmaChat"},
        "channelData": {"tenant": {"id": "tenant-1"}},
        "attachments": [
            {
                "contentType": "application/vnd.microsoft.teams.file.download.info",
                "name": "report.pdf",
                "content": {
                    "downloadUrl": "https://download.example/report",
                    "fileType": "pdf",
                    "fileSize": 1234,
                },
            }
        ],
    }

    event = await adapter.parse_inbound_event(payload, {})

    assert event is not None
    assert event.message_text == (
        "Files attached to this Teams message:\n"
        "- report.pdf | application/vnd.microsoft.teams.file.download.info | 1234 bytes "
        "| download_url=https://download.example/report"
    )
    assert event.metadata["attachments"] == [
        {
            "name": "report.pdf",
            "download_url": "https://download.example/report",
            "file_type": "pdf",
            "content_type": "application/vnd.microsoft.teams.file.download.info",
            "size": 1234,
        }
    ]


@pytest.mark.asyncio
async def test_teams_adapter_appends_attachment_details_to_text_message():
    adapter = TeamsSurfaceAdapter()
    payload = {
        "type": "message",
        "id": "msg-4",
        "text": "Lemma WHat does this image say? Describe it for me main andha hai.",
        "serviceUrl": "https://smba.example.test/teams",
        "from": {"id": "29:user", "aadObjectId": "aad-1", "name": "Rahul"},
        "conversation": {"id": "a:dm-conversation", "conversationType": "personal"},
        "recipient": {"id": "28:bot", "name": "LemmaChat"},
        "channelData": {"tenant": {"id": "tenant-1"}},
        "attachments": [
            {
                "contentType": "image/*",
                "contentUrl": "https://smba.example.test/v3/attachments/image-1/views/original",
                "name": "quote.png",
            }
        ],
    }

    event = await adapter.parse_inbound_event(payload, {})

    assert event is not None
    assert "Lemma WHat does this image say?" in event.message_text
    assert "Files attached to this Teams message:" in event.message_text
    assert "quote.png | image/*" in event.message_text
    assert (
        "download_url=https://smba.example.test/v3/attachments/image-1/views/original"
        in event.message_text
    )
    assert event.metadata["attachments"] == [
        {
            "name": "quote.png",
            "download_url": "https://smba.example.test/v3/attachments/image-1/views/original",
            "file_type": "png",
            "content_type": "image/*",
            "size": None,
        }
    ]


@pytest.mark.asyncio
async def test_teams_adapter_extracts_html_embedded_image_attachment():
    adapter = TeamsSurfaceAdapter()
    payload = {
        "type": "message",
        "id": "msg-5",
        "text": "Can you read this screenshot?",
        "serviceUrl": "https://smba.example.test/teams",
        "from": {"id": "29:user", "aadObjectId": "aad-1", "name": "Rahul"},
        "conversation": {"id": "a:dm-conversation", "conversationType": "personal"},
        "recipient": {"id": "28:bot", "name": "LemmaChat"},
        "channelData": {"tenant": {"id": "tenant-1"}},
        "attachments": [
            {
                "contentType": "text/html",
                "content": '<p><img alt="image" src="https://smba.example.test/v3/attachments/html-image/views/original" itemscope="png"></p>',
            }
        ],
    }

    event = await adapter.parse_inbound_event(payload, {})

    assert event is not None
    assert "Files attached to this Teams message:" in event.message_text
    assert (
        "download_url=https://smba.example.test/v3/attachments/html-image/views/original"
        in event.message_text
    )
    assert event.metadata["attachments"] == [
        {
            "name": "original",
            "download_url": "https://smba.example.test/v3/attachments/html-image/views/original",
            "file_type": "png",
            "content_type": "image/*",
            "size": None,
        }
    ]


@pytest.mark.asyncio
async def test_teams_adapter_extracts_generic_content_url_attachment():
    adapter = TeamsSurfaceAdapter()
    payload = {
        "type": "message",
        "id": "msg-6",
        "text": "Describe this image for me.",
        "serviceUrl": "https://smba.example.test/teams",
        "from": {"id": "29:user", "aadObjectId": "aad-1", "name": "Rahul"},
        "conversation": {"id": "a:dm-conversation", "conversationType": "personal"},
        "recipient": {"id": "28:bot", "name": "LemmaChat"},
        "channelData": {"tenant": {"id": "tenant-1"}},
        "attachments": [
            {
                "contentType": "reference",
                "name": "market_share 2.png",
                "contentUrl": "https://lemma.sharepoint.com/sites/SuperAGIWorkshopPvtLtd/Shared Documents/test_channel/market_share 2.png",
            }
        ],
    }

    event = await adapter.parse_inbound_event(payload, {})

    assert event is not None
    assert "Files attached to this Teams message:" in event.message_text
    assert "market_share 2.png | reference" in event.message_text
    assert (
        "download_url=https://lemma.sharepoint.com/sites/SuperAGIWorkshopPvtLtd/Shared Documents/test_channel/market_share 2.png"
        in event.message_text
    )
    assert event.metadata["attachments"] == [
        {
            "name": "market_share 2.png",
            "download_url": "https://lemma.sharepoint.com/sites/SuperAGIWorkshopPvtLtd/Shared Documents/test_channel/market_share 2.png",
            "file_type": "png",
            "content_type": "reference",
            "size": None,
        }
    ]


@pytest.mark.asyncio
async def test_teams_adapter_extracts_inline_image_from_message_html():
    adapter = TeamsSurfaceAdapter()
    payload = {
        "type": "message",
        "id": "msg-7",
        "text": '<p>Lemma Brother describe this image for me.</p><p><img src="https://smba.example.test/v3/attachments/inline-image/views/original" /></p>',
        "serviceUrl": "https://smba.example.test/teams",
        "from": {"id": "29:user", "aadObjectId": "aad-1", "name": "Rahul"},
        "conversation": {"id": "a:dm-conversation", "conversationType": "personal"},
        "recipient": {"id": "28:bot", "name": "LemmaChat"},
        "channelData": {"tenant": {"id": "tenant-1"}},
        "attachments": [],
    }

    event = await adapter.parse_inbound_event(payload, {})

    assert event is not None
    assert "Lemma Brother describe this image for me." in event.message_text
    assert "Files attached to this Teams message:" in event.message_text
    assert (
        "download_url=https://smba.example.test/v3/attachments/inline-image/views/original"
        in event.message_text
    )
    assert event.metadata["attachments"] == [
        {
            "name": "original",
            "download_url": "https://smba.example.test/v3/attachments/inline-image/views/original",
            "file_type": "",
            "content_type": "image/*",
            "size": None,
        }
    ]


@pytest.mark.asyncio
async def test_teams_enrich_inbound_event_fetches_current_message_attachments_from_graph(
    monkeypatch,
):
    adapter = TeamsSurfaceAdapter()
    event = ParsedInboundSurfaceEvent(
        platform="TEAMS",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        tenant_id="tenant-1",
        external_channel_id="19:channel@thread.tacv2",
        external_thread_id="msg-9",
        external_message_id="msg-9",
        sender_external_user_id="29:user",
        sender_display_name="Rahul",
        message_text="Lemma what does this image say. Describe it for me",
        mentioned_agent=True,
        reply_target={
            "team_id": "19:team@thread.tacv2",
            "channel_id": "19:channel@thread.tacv2",
            "service_url": "https://smba.example.test/teams",
        },
        metadata={
            "team_id": "19:team@thread.tacv2",
            "channel_id": "19:channel@thread.tacv2",
        },
    )

    monkeypatch.setattr(
        adapter, "_get_graph_token", AsyncMock(return_value="graph-token")
    )
    monkeypatch.setattr(
        teams_adapter_module.client,
        "resolve_graph_team_id",
        AsyncMock(return_value="11111111-2222-4333-8444-555555555555"),
    )
    monkeypatch.setattr(
        teams_adapter_module.client,
        "get_json",
        AsyncMock(
            return_value={
                "id": "msg-9",
                "body": {
                    "content": "<div>Lemma what does this image say. Describe it for me</div>"
                },
                "attachments": [
                    {
                        "name": "diagram.png",
                        "contentType": "image/png",
                        "contentUrl": "https://files.example/diagram.png",
                    }
                ],
            }
        ),
    )

    enriched = await adapter.enrich_inbound_event(credentials={}, event=event)

    assert enriched.metadata["attachments"] == [
        {
            "name": "diagram.png",
            "download_url": "https://files.example/diagram.png",
            "file_type": "png",
            "content_type": "image/png",
            "size": None,
        }
    ]
    assert "Files attached to this Teams message:" in enriched.message_text
    assert (
        "diagram.png | image/png | download_url=https://files.example/diagram.png"
        in enriched.message_text
    )
