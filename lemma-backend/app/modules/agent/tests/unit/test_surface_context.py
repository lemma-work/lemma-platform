"""A run's surface fields, from the place agent_surfaces recorded."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.run_state import RunState
from app.modules.agent.services.surface_context import surface_context_for_run
from app.modules.agent_surfaces.contracts.conversations import ConversationSurface

pytestmark = pytest.mark.unit


def test_a_conversation_no_surface_heard_reads_as_all_nulls() -> None:
    context = surface_context_for_run(None, RunState())

    assert set(context) == {
        "surface_id",
        "surface_platform",
        "surface_metadata",
        "external_channel_id",
        "external_thread_id",
        "external_user_id",
        "external_message_id",
        "agent_display_name",
        "surface_conversation_kind",
        "place_kind",
        "on_web_widget",
    }
    assert not any(context.values())


def test_the_recorded_place_is_passed_through() -> None:
    surface_id = uuid4()
    place = ConversationSurface(
        surface_id=surface_id,
        surface_platform="TELEGRAM",
        external_channel_id="-100123",
        external_user_id="42",
        conversation_kind="DM",
    )

    context = surface_context_for_run(place, RunState())

    assert context["surface_id"] == surface_id
    assert context["surface_platform"] == "TELEGRAM"
    assert context["external_channel_id"] == "-100123"
    assert context["external_user_id"] == "42"
    assert context["surface_conversation_kind"] == "DM"


def test_event_metadata_of_an_older_shape_still_reaches_the_run() -> None:
    raw = {"platform": "SLACK", "attachments": "not a list"}

    context = surface_context_for_run(
        ConversationSurface(surface_platform="SLACK", surface_event_metadata=raw),
        RunState(),
    )

    assert context["surface_metadata"] == raw


def test_a_run_asked_in_lemma_talks_over_no_platform() -> None:
    """One conversation serves a member across Lemma and WhatsApp: the
    platform it was last heard on is not where a line in Lemma was asked."""
    heard_on_whatsapp = ConversationSurface(
        surface_id=uuid4(),
        surface_platform="WHATSAPP",
        external_channel_id="15556295168",
        external_user_id="919800000000",
        conversation_kind="DM",
    )

    asked_in_lemma = surface_context_for_run(
        heard_on_whatsapp, RunState(asked_in_lemma=True)
    )

    # Talks over none, but it is still a place the contact reads alone.
    assert asked_in_lemma.pop("place_kind") == "DM"
    assert not any(asked_in_lemma.values())
    ordinary = surface_context_for_run(heard_on_whatsapp, RunState())
    assert ordinary["surface_platform"] == "WHATSAPP"
