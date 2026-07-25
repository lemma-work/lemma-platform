from uuid import uuid4

import pytest

from app.core.config import settings
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfaceConfig,
    SurfaceMode,
    SurfacePlatform,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    _pod_access_message,
    nonmember_context,
    signup_message,
    surface_setup_message,
)


def test_shared_surface_fallback_urls_use_their_matching_frontends(monkeypatch):
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test/")
    monkeypatch.setattr(
        settings,
        "auth_frontend_url",
        "https://auth.example.test/auth/",
    )
    pod_id = uuid4()

    assert signup_message() == (
        "Please sign up before chatting with this agent. "
        "You can get started here: https://auth.example.test/auth"
    )
    assert surface_setup_message() == (
        "You're signed in, but no agent surface is configured for you yet. "
        "Open Lemma to set up or select a surface: https://app.example.test"
    )
    assert _pod_access_message(pod_id) == (
        "You're signed up, but don't have access to this workspace yet. "
        f"Request access here: https://app.example.test/pod/{pod_id}"
    )


@pytest.mark.parametrize("platform", list(SurfacePlatform))
def test_pod_access_url_is_shared_across_every_surface_platform(
    monkeypatch,
    platform,
):
    """All adapters receive the same frontend route from the shared fallback."""
    monkeypatch.setattr(settings, "frontend_url", "https://app.example.test/")
    pod_id = uuid4()
    surface = AgentSurfaceEntity(
        id=uuid4(),
        pod_id=pod_id,
        name=f"{platform.value.lower()}-access-link",
        agent_id=uuid4(),
        surface_type=platform,
        mode=SurfaceMode.EMAIL if platform.is_email else SurfaceMode.DM,
        account_id=uuid4(),
        config=SurfaceConfig(),
        is_active=True,
    )
    event = ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_channel_id="channel-1",
        external_thread_id="thread-1",
        external_message_id="message-1",
        sender_external_user_id="user-1",
        message_text="hello",
        is_dm=True,
        reply_target={},
    )

    context = nonmember_context(
        surface=surface,
        parsed=event,
        agent_display_name="Surface Agent",
    )

    assert context is not None
    assert context.platform is platform
    assert context.reply_kind == "pod_access"
    assert context.reply_message == (
        "You're signed up, but don't have access to this workspace yet. "
        f"Request access here: https://app.example.test/pod/{pod_id}"
    )
