"""What a Telegram sender nobody resolved is told.

A Telegram @username proves nothing (see ``test_identity_resolution``), so
every unresolved Telegram sender -- whatever handle they write with, and
whichever Lemma profile once named it -- gets the same answer: share your own
contact, and the number in it is matched against verified numbers.
"""

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
from app.modules.agent_surfaces.platforms.telegram.adapter import (
    TelegramSurfaceAdapter,
)
from app.modules.agent_surfaces.services.fallback_reply_service import (
    unresolved_sender_context,
)

pytestmark = pytest.mark.unit


def _event(*, is_dm: bool = True, **metadata: object) -> ParsedInboundSurfaceEvent:
    shared = metadata.get("contact_shared_by_sender") is True
    return ParsedInboundSurfaceEvent(
        platform=SurfacePlatform.TELEGRAM,
        conversation_type=(
            ConversationType.EXTERNAL_DM if is_dm else ConversationType.EXTERNAL_GROUP
        ),
        external_thread_id="700",
        external_message_id="1",
        sender_external_user_id="700",
        sender_phone="15550100" if shared else None,
        message_text="" if shared else "hello",
        is_dm=is_dm,
        metadata={"sender_username": "asha", **metadata},
    )


def _context(event: ParsedInboundSurfaceEvent):
    return unresolved_sender_context(
        surface=AgentSurfaceEntity(
            id=uuid4(),
            pod_id=uuid4(),
            name="telegram",
            agent_id=uuid4(),
            surface_type=SurfacePlatform.TELEGRAM,
            pod_connector_slug="own-bot",
            config=SurfaceConfig(),
            is_active=True,
        ),
        parsed=event,
        adapter=TelegramSurfaceAdapter(),
        agent_display_name="Lem",
    )


def test_a_sender_with_a_handle_is_asked_for_their_contact_like_anyone() -> None:
    context = _context(_event())

    assert context is not None
    assert context.reply_kind == "identity_link"
    keyboard = context.reply_metadata["reply_markup"]["keyboard"]
    assert keyboard[0][0]["request_contact"] is True
    # Nothing about a handle or the account it might belong to is disclosed.
    assert "username" not in context.reply_message


def test_someone_elses_contact_is_refused() -> None:
    context = _context(_event(contact_shared_by_sender=False))

    assert context is not None
    assert "your own phone number" in context.reply_message


def test_a_contact_that_matched_no_verified_number_is_sent_to_signup() -> None:
    context = _context(_event(contact_shared_by_sender=True))

    assert context is not None
    assert context.reply_kind == "signup"


def test_an_unresolved_sender_in_a_group_is_not_asked_in_front_of_the_room() -> None:
    assert _context(_event(is_dm=False)) is None
