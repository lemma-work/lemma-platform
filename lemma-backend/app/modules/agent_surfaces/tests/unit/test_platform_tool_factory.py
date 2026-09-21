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
        surface_identity_email="ops.acme@ops.lemma.work",
    )

    monkeypatch.setattr("app.core.config.settings.resend_api_key", "re_test")

    credentials = await SurfaceCredentialResolver(uow=None).for_surface(
        surface, prefer_native=True
    )

    assert credentials["api_key"] == "re_test"
    assert credentials["from_address"] == "ops.acme@ops.lemma.work"


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


@pytest.mark.asyncio
async def test_a_pooled_surfaces_tools_are_built_with_that_numbers_credentials(
    monkeypatch,
):
    """The fast path that skipped the pool, and so skipped the whole feature.

    `has_native_credentials` is true for any deployment with WhatsApp settings
    at all, which is all of them, so the factory took a shortcut straight to
    `native_credentials` -- a pure function over settings that has never heard
    of the pool. Every tool an agent called on a surface holding a pooled number
    was therefore built with the *deployment's* token and phone number id, and
    sent as a number the person had never written to.

    The resolver is the one place that layers a held number's credentials over
    the settings ones, so the fix is to go through it. `prefer_native` is the
    shortcut, kept as a flag rather than as a second code path.
    """
    from datetime import datetime, timezone

    from app.modules.agent_surfaces.infrastructure.whatsapp_pool_models import (
        WhatsAppNumber,
    )
    from app.modules.test_support.mappers import configure_test_mappers

    configure_test_mappers()

    row = WhatsAppNumber(
        id=uuid4(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        phone_number_id="pool-b",
        display_phone_number="+15550001111",
        waba_id="waba-b",
        access_token="the-pools-token",
        status="AVAILABLE",
    )

    class _Result:
        def scalar_one_or_none(self):
            return row

    class _Session:
        async def execute(self, _statement):
            return _Result()

    class _PooledUoW:
        def __init__(self) -> None:
            self.session = _Session()

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return None

    surface = AgentSurfaceEntity.create(
        surface_type=SurfacePlatform.WHATSAPP,
        pod_id=uuid4(),
        agent_id=uuid4(),
        config=SurfaceConfig(),
        surface_identity_id="pool-b",
    )

    async def fake_get(self, surface_id):
        return surface

    monkeypatch.setattr(
        "app.modules.agent_surfaces.infrastructure.adapters.platform_tool_factory.SurfaceRepository.get",
        fake_get,
    )
    monkeypatch.setattr(surface_settings, "whatsapp_access_token", "deployment-token")
    monkeypatch.setattr(surface_settings, "whatsapp_phone_number_id", "deployment-pn")
    monkeypatch.setattr(surface_settings, "whatsapp_waba_id", "deployment-waba")

    built: list[dict] = []
    monkeypatch.setattr(
        "app.modules.agent_surfaces.infrastructure.adapters.platform_tool_factory._TOOLSET_BUILDERS",
        {"WHATSAPP": lambda *, credentials: built.append(credentials) or object()},
    )

    factory = SurfacePlatformToolFactory(uow_factory=lambda: _PooledUoW())
    await factory.build_toolsets(conversation=_conversation_for_surface(surface))

    assert built, "no WhatsApp toolset was built at all"
    assert built[0]["access_token"] == "the-pools-token", (
        "the agent's tools were handed the deployment's token, so every send "
        "went out as a number the recipient has never seen"
    )
    assert built[0]["phone_number_id"] == "pool-b"
