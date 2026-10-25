"""A surface does not answer its own message when the platform hands it back."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    PLATFORM_CAPABILITIES,
)
from app.modules.agent_surfaces.services.sender_resolver import is_self_addressed

pytestmark = pytest.mark.unit

BOT_ID = "U0BOT"


def _surface(platform: SurfacePlatform) -> AgentSurfaceEntity:
    surface = AgentSurfaceEntity.create(
        surface_type=platform,
        pod_id=uuid4(),
        agent_id=uuid4(),
        config=SurfaceConfig(),
        pod_connector_slug="own-bot",
        surface_identity_id=BOT_ID,
    )
    return surface.model_copy(
        update={"surface_identity_email": "agent@mail.example.com"}
    )


def _event(
    platform: SurfacePlatform, *, sender: str | None, email: str | None = None
) -> ParsedInboundSurfaceEvent:
    return ParsedInboundSurfaceEvent(
        platform=platform,
        conversation_type=ConversationType.EXTERNAL_DM,
        external_thread_id="thread-1",
        sender_external_user_id=sender,
        sender_email=email,
        message_text="hello",
        is_dm=True,
    )


def test_echoing_platforms_are_exactly_slack():
    echoing = {p for p, c in PLATFORM_CAPABILITIES.items() if c.echoes_own_messages}
    assert echoing == {"SLACK"}


def test_slack_message_from_the_bot_itself_is_self_addressed():
    surface = _surface(SurfacePlatform.SLACK)
    assert is_self_addressed(
        surface=surface, parsed=_event(SurfacePlatform.SLACK, sender=BOT_ID)
    )


def test_slack_message_from_a_person_or_nobody_is_not_self_addressed():
    surface = _surface(SurfacePlatform.SLACK)
    assert not is_self_addressed(
        surface=surface, parsed=_event(SurfacePlatform.SLACK, sender="U0PERSON")
    )
    assert not is_self_addressed(
        surface=surface, parsed=_event(SurfacePlatform.SLACK, sender=None)
    )


@pytest.mark.parametrize(
    "platform",
    [SurfacePlatform.TEAMS, SurfacePlatform.TELEGRAM, SurfacePlatform.WHATSAPP],
)
def test_non_echoing_platform_sender_matching_identity_is_not_self_addressed(
    platform,
):
    assert not is_self_addressed(
        surface=_surface(platform), parsed=_event(platform, sender=BOT_ID)
    )


def test_email_from_the_surface_own_address_is_self_addressed():
    surface = _surface(SurfacePlatform.RESEND)
    assert is_self_addressed(
        surface=surface,
        parsed=_event(
            SurfacePlatform.RESEND, sender=None, email="Agent@Mail.Example.com"
        ),
    )
    assert not is_self_addressed(
        surface=surface,
        parsed=_event(SurfacePlatform.RESEND, sender=None, email="a@b.example"),
    )
