"""Reading a surface off a conversation, including when the metadata is wrong.

`surface_context_for_run` narrows six metadata values to `str`. That
narrowing is deliberate: a stored value of another type used to travel into
`ConversationContext` and fail pydantic there, taking the whole run down, and a
run that cannot start is a worse answer than a reply missing one field.

Dropping a field quietly is still a loss, though -- a reply with no
`external_channel_id` has nowhere to go -- so the drop is warned about. These
tests hold both halves: the field goes, and the log says which one.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import ConversationType
from app.modules.agent.domain.run_state import RunState
from app.modules.agent.services.surface_context import surface_context_for_run

pytestmark = pytest.mark.unit


def _conversation(metadata: dict | None) -> Conversation:
    return Conversation(
        id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        type=ConversationType.CHAT,
        metadata=metadata,
    )


def _ordinary_run(conversation: Conversation):
    """A run a surface or a person in the conversation itself started."""
    return surface_context_for_run(conversation, RunState())


def test_a_conversation_with_no_surface_reads_as_all_nulls() -> None:
    context = _ordinary_run(_conversation(None))

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
    }
    assert all(value is None for value in context.values())


def test_stored_strings_are_passed_through() -> None:
    surface_id = uuid4()

    context = _ordinary_run(
        _conversation(
            {
                "surface_id": str(surface_id),
                "surface_platform": "TELEGRAM",
                "external_channel_id": "-100123",
                "external_user_id": "42",
            }
        )
    )

    assert context["surface_id"] == surface_id
    assert context["surface_platform"] == "TELEGRAM"
    assert context["external_channel_id"] == "-100123"
    assert context["external_user_id"] == "42"


def test_a_value_of_the_wrong_type_is_dropped_rather_than_raised(caplog) -> None:
    """Every surface stringifies these already, so this is the never case.

    A Telegram chat id is an int on the wire and `str()`-ed by its adapter. If
    one ever arrives unconverted the run still starts, minus that field.
    """
    with caplog.at_level("WARNING"):
        context = _ordinary_run(
            _conversation({"external_channel_id": -100123, "external_user_id": "42"})
        )

    assert context["external_channel_id"] is None
    assert context["external_user_id"] == "42", "one bad field does not take the rest"


def test_the_dropped_field_is_named_in_a_warning(caplog) -> None:
    with caplog.at_level("WARNING"):
        _ordinary_run(_conversation({"external_channel_id": -100123}))

    assert "non_text_metadata_dropped" in caplog.text
    assert "external_channel_id" in caplog.text


def test_well_formed_metadata_says_nothing(caplog) -> None:
    with caplog.at_level("WARNING"):
        _ordinary_run(_conversation({"external_channel_id": "-100123"}))

    assert "non_text_metadata_dropped" not in caplog.text


def test_a_field_that_is_absent_is_not_a_dropped_field(caplog) -> None:
    """Missing and wrong are different. Only wrong is worth a line."""
    with caplog.at_level("WARNING"):
        _ordinary_run(_conversation({"surface_platform": "SLACK"}))

    assert "non_text_metadata_dropped" not in caplog.text


def test_a_run_asked_in_lemma_talks_over_no_platform() -> None:
    """One conversation serves a member across Lemma and WhatsApp: the
    platform it was last heard on is not where a line in Lemma was asked."""
    heard_on_whatsapp = _conversation(
        {
            "surface_id": str(uuid4()),
            "surface_platform": "WHATSAPP",
            "external_channel_id": "15556295168",
            "external_user_id": "919800000000",
            "conversation_kind": "DM",
        }
    )

    asked_in_lemma = surface_context_for_run(
        heard_on_whatsapp, RunState(asked_in_lemma=True)
    )

    assert all(value is None for value in asked_in_lemma.values())
    assert _ordinary_run(heard_on_whatsapp)["surface_platform"] == "WHATSAPP"
