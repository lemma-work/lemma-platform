from __future__ import annotations

from uuid import uuid4

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfaceChannelRoute,
    SurfaceConfig,
    SurfaceMode,
    SurfacePlatform,
)


def _telegram_surface() -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="telegram",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.TELEGRAM,
        mode=SurfaceMode.DM,
        account_id=None,
        config=SurfaceConfig(channels=[SurfaceChannelRoute(channel_id="G1")]),
        is_active=True,
    )


def _telegram_event(*, is_dm: bool, mentioned: bool) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="TELEGRAM",
        conversation_type=(
            ConversationType.EXTERNAL_DM if is_dm else ConversationType.EXTERNAL_GROUP
        ),
        external_channel_id="G1",
        external_thread_id="G1",
        message_text="hello",
        is_dm=is_dm,
        mentioned_agent=mentioned,
        # Telegram parser always sets this True; it must NOT bypass mention
        # gating in groups.
        should_start_conversation=True,
    )


def test_telegram_dm_always_allowed_even_without_mention():
    surface = _telegram_surface()
    event = _telegram_event(is_dm=True, mentioned=False)
    assert surface.allows_inbound_event(event) is True


def test_telegram_group_requires_mention():
    surface = _telegram_surface()
    # No mention in a group → dropped (the should_start_conversation bypass bug).
    assert (
        surface.allows_inbound_event(_telegram_event(is_dm=False, mentioned=False))
        is False
    )
    # Mentioned in a group → allowed.
    assert (
        surface.allows_inbound_event(_telegram_event(is_dm=False, mentioned=True))
        is True
    )


def test_telegram_group_thread_reply_allowed_without_mention():
    surface = _telegram_surface()
    event = _telegram_event(is_dm=False, mentioned=False)
    event.metadata["is_thread_reply"] = True
    assert surface.allows_inbound_event(event) is True
