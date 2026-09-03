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


def _slack_surface(*, bot_user_id: str | None) -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="slack",
        agent_id=uuid4(),
        surface_type=SurfacePlatform.SLACK,
        mode=SurfaceMode.DM,
        account_id=None,
        config=SurfaceConfig(channels=[SurfaceChannelRoute(channel_id="C1")]),
        is_active=True,
        surface_identity_id=bot_user_id,
    )


def _slack_channel_event(
    *, event_type: str, mentioned_user_ids: list[str]
) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform="SLACK",
        conversation_type=ConversationType.EXTERNAL_GROUP,
        external_channel_id="C1",
        external_thread_id="1700000000.000200",
        message_text="hey <@U-COLLEAGUE> can you look at this",
        is_dm=False,
        # What the parser sets: true as soon as *anybody* was @-mentioned.
        mentioned_agent=True,
        metadata={
            "event_type": event_type,
            "mentioned_user_ids": mentioned_user_ids,
        },
    )


def test_slack_channel_message_mentioning_somebody_else_is_refused():
    surface = _slack_surface(bot_user_id="U-BOT")
    event = _slack_channel_event(
        event_type="message", mentioned_user_ids=["U-COLLEAGUE"]
    )
    assert surface.allows_inbound_event(event) is False


def test_slack_channel_message_mentioning_the_bot_is_allowed():
    surface = _slack_surface(bot_user_id="U-BOT")
    event = _slack_channel_event(
        event_type="message", mentioned_user_ids=["U-COLLEAGUE", "U-BOT"]
    )
    assert surface.allows_inbound_event(event) is True


def test_slack_surface_without_a_recorded_bot_id_stays_out_of_the_channel():
    """A surface that cannot say whether it was mentioned must not answer.

    ``surface_identity_id`` is unset on a Slack surface created through the
    bundle/CLI path, and the gate used to read that as "allow": the agent then
    replied to every channel message in which any colleague was @-mentioned.
    """
    surface = _slack_surface(bot_user_id=None)
    event = _slack_channel_event(
        event_type="message", mentioned_user_ids=["U-COLLEAGUE"]
    )
    assert surface.allows_inbound_event(event) is False


def test_slack_app_mention_is_allowed_without_a_recorded_bot_id():
    """Slack only fires ``app_mention`` for the app that was mentioned.

    So the event type is itself the answer, and a surface with no recorded bot
    id still responds to a direct @mention -- which is what fail-closed on the
    plain ``message`` event must not take away.
    """
    surface = _slack_surface(bot_user_id=None)
    event = _slack_channel_event(event_type="app_mention", mentioned_user_ids=[])
    assert surface.allows_inbound_event(event) is True
