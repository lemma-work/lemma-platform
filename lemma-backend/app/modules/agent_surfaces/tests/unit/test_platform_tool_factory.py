from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.agent_surfaces.config import surface_settings
from app.modules.agent.domain.entities import Conversation
from app.modules.agent_surfaces.domain.entities import (
    SurfacePlatform,
    AgentSurfaceEntity,
    SurfaceConfig,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.infrastructure.adapters.platform_tool_factory import (
    SurfacePlatformToolFactory,
)


class _FakeUoW:
    def __init__(self) -> None:
        self.session = AsyncMock()

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None


def _conversation_for_surface(surface: AgentSurfaceEntity) -> Conversation:
    return Conversation(
        id=uuid4(),
        pod_id=surface.pod_id,
        agent_id=surface.agent_id,
        user_id=uuid4(),
        title="external surface chat",
        metadata={
            "source": "agent_surfaces",
            "surface_id": str(surface.id),
            "surface_platform": surface.surface_type.value,
        },
    )


@pytest.mark.asyncio
async def test_platform_tool_factory_uses_native_whatsapp_credentials(
    monkeypatch,
):
    surface = AgentSurfaceEntity.create(
        surface_type=SurfacePlatform.WHATSAPP,
        pod_id=uuid4(),
        agent_id=uuid4(),
        config=SurfaceConfig(),
    )
    factory = SurfacePlatformToolFactory(uow_factory=lambda: _FakeUoW())

    async def fake_get(self, surface_id):
        assert surface_id == surface.id
        return surface

    monkeypatch.setattr(
        "app.modules.agent_surfaces.infrastructure.adapters.platform_tool_factory.SurfaceRepository.get",
        fake_get,
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "native-wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "phone-123")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-123")

    toolsets = await factory.build_toolsets(
        conversation=_conversation_for_surface(surface)
    )

    assert len(toolsets) == 1
    assert "whatsapp_get_current_contact" in toolsets[0].tools
    assert "whatsapp_send_file" not in toolsets[0].tools


@pytest.mark.asyncio
async def test_platform_tool_factory_uses_native_telegram_env_credentials(
    monkeypatch,
):
    surface = AgentSurfaceEntity.create(
        surface_type=SurfacePlatform.TELEGRAM,
        pod_id=uuid4(),
        agent_id=uuid4(),
        config=SurfaceConfig(),
    )
    factory = SurfacePlatformToolFactory(uow_factory=lambda: _FakeUoW())

    async def fake_get(self, surface_id):
        assert surface_id == surface.id
        return surface

    monkeypatch.setattr(
        "app.modules.agent_surfaces.infrastructure.adapters.platform_tool_factory.SurfaceRepository.get",
        fake_get,
    )
    monkeypatch.setattr(surface_settings, "telegram_bot_token", "native-telegram-token")

    toolsets = await factory.build_toolsets(
        conversation=_conversation_for_surface(surface)
    )

    assert len(toolsets) == 1
    assert "telegram_get_current_chat" in toolsets[0].tools
    assert "telegram_send_file" not in toolsets[0].tools


@pytest.mark.asyncio
async def test_platform_tool_factory_adds_native_whatsapp_tools_for_default_agent_conversation(
    monkeypatch,
):
    conversation = Conversation(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=None,
        user_id=uuid4(),
        title="default agent whatsapp chat",
        metadata={"source": "agent_surfaces", "surface_platform": "WHATSAPP"},
    )
    factory = SurfacePlatformToolFactory(uow_factory=lambda: _FakeUoW())

    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "native-wa-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "phone-123")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "waba-123")

    toolsets = await factory.build_toolsets(conversation=conversation)

    assert len(toolsets) == 1
    assert "whatsapp_get_current_contact" in toolsets[0].tools
    assert "whatsapp_send_file" not in toolsets[0].tools


async def test_the_resend_reply_tool_is_given_the_surfaces_from_address(monkeypatch):
    """`from_address` belongs to the surface row, not to the platform.

    The factory used to take a native-credentials shortcut whenever the
    deployment had a Resend api key — which is always — and that shortcut knows
    nothing about the surface, so the reply tool was built without a sender.
    Every `resend_reply_email` call then failed with "Resend send requires
    api_key, from_address and a recipient", including the acknowledgement that
    tells somebody their answer was recorded.
    """
    from uuid import uuid4

    from app.modules.agent_surfaces.domain.entities import (
        AgentSurfaceEntity,
        SurfaceConfig,
        SurfacePlatform,
    )

    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        agent_id=uuid4(),
        name="resend-ops",
        surface_type=SurfacePlatform.RESEND,
        config=SurfaceConfig(),
        surface_identity_email="ops.acme@ops.asur.work",
    )

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_test")

    credentials = await SurfaceCredentialResolver(uow=None).for_surface(
        surface, prefer_native=True
    )

    assert credentials["api_key"] == "re_test"
    assert credentials["from_address"] == "ops.acme@ops.asur.work"


@pytest.mark.parametrize("platform", ["RESEND"])
async def test_an_email_surface_builds_no_platform_toolset(platform):
    """Their only tool was the reply, and the observer sends that now.

    Not an oversight to be filled in later: an email surface has nothing left
    for a platform toolset to carry.
    """
    from app.modules.agent_surfaces.infrastructure.adapters.platform_tool_factory import (
        _TOOLSET_BUILDERS,
    )

    assert platform not in _TOOLSET_BUILDERS
