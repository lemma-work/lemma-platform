from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.tools.context import ConversationContext
from app.modules.agent_surfaces.domain.surface_event_metadata import (
    WhatsAppSurfaceEventMetadata,
)
from app.modules.agent_surfaces.platforms.whatsapp.tools import (
    build_whatsapp_surface_toolset,
)


def test_whatsapp_toolset_generates_tool_descriptions():
    toolset = build_whatsapp_surface_toolset(credentials={})

    contact_tool = toolset.tools["whatsapp_get_current_contact"]

    assert contact_tool.description
    assert contact_tool.function_schema.description
    # File send/download tools were removed; files flow via auto-ingest +
    # display_resource now.
    assert "whatsapp_send_file" not in toolset.tools
    assert "whatsapp_download_file" not in toolset.tools


@pytest.mark.asyncio
async def test_whatsapp_get_current_contact_returns_metadata():
    toolset = build_whatsapp_surface_toolset(
        credentials={"phone_number_id": "phone-123"}
    )
    tool = toolset.tools["whatsapp_get_current_contact"]

    ctx = SimpleNamespace(
        deps=ConversationContext(
            user_id=uuid4(),
            pod_id=uuid4(),
            conversation_id=uuid4(),
            surface_platform="WHATSAPP",
            external_channel_id="phone-123",
            external_thread_id="919999999999@phone-123",
            surface_metadata=WhatsAppSurfaceEventMetadata(
                phone_number_id="phone-123",
                waba_id="waba-123",
                contacts=[{"wa_id": "919999999999", "profile": {"name": "Asha"}}],
                attachments=[
                    {"id": "media-1", "name": "photo.jpg", "content_type": "image"}
                ],
            ),
        )
    )

    response = await tool.function(ctx, SimpleNamespace())

    assert response.success is True
    assert response.wa_id == "919999999999"
    assert response.display_name == "Asha"
    assert response.phone_number_id == "phone-123"
    assert response.waba_id == "waba-123"
    assert response.attachment_names == ["photo.jpg"]
