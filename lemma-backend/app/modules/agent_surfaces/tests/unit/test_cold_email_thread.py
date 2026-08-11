"""The coordinate arithmetic that decides whether a reply is ever seen again.

Pure functions, so the part of cold-open email that fails *silently* — a thread
id that does not match what the inbound parser derives — can be tested without a
mail provider or a database.
"""

from __future__ import annotations

from uuid import uuid4

from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfaceConfig,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.models import ColdEmailSendResult
from app.modules.agent_surfaces.services.cold_email_thread import (
    MAX_THREAD_ID_LENGTH,
    build_cold_email_thread,
    cold_thread_seed_id,
)


def _surface(address: str | None = "pod-1@ops.asur.work") -> AgentSurfaceEntity:
    return AgentSurfaceEntity(
        id=uuid4(),
        pod_id=uuid4(),
        name="resend",
        surface_type=SurfacePlatform.RESEND,
        config=SurfaceConfig(),
        surface_identity_email=address,
    )


def test_the_seed_is_a_message_id_that_fits_the_column():
    """``external_thread_id`` is String(255).

    A seed longer than that is truncated on write and then never matches the
    reply — the failure looks like "they just never answered".
    """
    seed = cold_thread_seed_id(notification_id=uuid4(), surface=_surface())

    assert seed.startswith("<lemma-notification-")
    assert seed.endswith("@ops.asur.work>")
    assert len(seed) <= MAX_THREAD_ID_LENGTH


def test_the_same_notification_always_seeds_the_same_thread():
    """Why the seed is derived, not random: re-delivery must not fork a thread."""
    notification_id = uuid4()
    surface = _surface()

    assert cold_thread_seed_id(
        notification_id=notification_id, surface=surface
    ) == cold_thread_seed_id(notification_id=notification_id, surface=surface)


def test_a_surface_with_no_address_still_yields_a_valid_message_id():
    """RFC 2606 reserves ``.invalid``, so it can never resolve to a real host."""
    seed = cold_thread_seed_id(notification_id=uuid4(), surface=_surface(None))

    assert seed.startswith("<") and seed.endswith(">")
    assert "@lemma.invalid>" in seed


def test_the_stored_event_parses_back_as_an_inbound_event():
    """It is written to ``link.last_event``, and egress refuses an unparseable one.

    ``_resolve_egress_target`` bails when a link's last event will not validate,
    so a malformed blob here means the agent's own next message in this
    conversation goes nowhere — with no error anywhere.
    """
    surface = _surface()
    thread = build_cold_email_thread(
        surface=surface,
        recipient_email="Bob@Example.com",
        sent=ColdEmailSendResult(
            external_thread_id="<seed@ops.asur.work>",
            external_message_id="email-9",
            reply_target={"recipient_email": "Bob@Example.com", "subject": "Standup"},
        ),
    )

    event = ParsedInboundSurfaceEvent.model_validate(thread.last_event)
    assert event.external_thread_id == "<seed@ops.asur.work>"
    # Both sides lowercased: the parser records the sender that way, and the
    # match is exact.
    assert event.sender_external_user_id == "bob@example.com"
    assert thread.external_channel_id == "pod-1@ops.asur.work"
