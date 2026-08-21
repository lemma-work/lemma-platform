from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.tools.context import ConversationContext
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    TelegramSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms.telegram.tools import (
    build_telegram_surface_toolset,
)


class _FakeHttpResponse:
    def __init__(self, *, json_data=None, content: bytes = b"") -> None:
        self._json_data = json_data or {}
        self.content = content

    def raise_for_status(self) -> None:
        return None

    def json(self):
        return self._json_data


def test_telegram_toolset_generates_tool_descriptions():
    toolset = build_telegram_surface_toolset(credentials={})

    chat_tool = toolset.tools["telegram_get_current_chat"]
    assert chat_tool.description
    assert chat_tool.function_schema.description
    # File send/download tools were removed; files flow via auto-ingest +
    # display_resource now.
    assert "telegram_send_file" not in toolset.tools
    assert "telegram_download_file" not in toolset.tools


@pytest.mark.asyncio
async def test_telegram_get_current_chat_returns_metadata():
    toolset = build_telegram_surface_toolset(
        credentials={"bot_token": "telegram-token"}
    )
    tool = toolset.tools["telegram_get_current_chat"]

    ctx = SimpleNamespace(
        deps=ConversationContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            surface_platform="TELEGRAM",
            external_channel_id="12345",
            external_thread_id="topic-7",
            surface_metadata=TelegramSurfaceEventMetadata(
                chat_type="supergroup",
                chat_id="12345",
                message_thread_id="topic-7",
                is_topic_message=True,
                attachments=[
                    {
                        "file_id": "file-123",
                        "name": "voice-note.ogg",
                        "content_type": "audio",
                    }
                ],
            ),
        )
    )

    response = await tool.function(ctx, SimpleNamespace())

    assert response.success is True
    assert response.chat_id == "12345"
    assert response.chat_type == "supergroup"
    assert response.message_thread_id == "topic-7"
    assert response.is_topic_message is True
    assert response.attachment_names == ["voice-note.ogg"]
