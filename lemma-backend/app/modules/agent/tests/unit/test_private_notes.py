"""A note to the agent, written in Lemma into a conversation a group can read.

Every run in such a conversation answers on the chat platform, which is right
for the group's own questions and wrong for "what should we tell him?". The
note marks its run, and the history every later turn reads labels the note, so
the model is told it was for it alone.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.modules.agent.domain.private_notes import (
    PRIVATE_NOTE_KEY,
    PRIVATE_NOTE_LABEL,
    is_private_note,
    run_is_private,
    run_metadata_for,
)
from app.modules.agent.infrastructure.harnesses.pydantic_ai_history import (
    user_prompt_text,
)

pytestmark = pytest.mark.unit


def test_a_note_marks_the_run_it_starts():
    metadata = run_metadata_for({PRIVATE_NOTE_KEY: True})

    assert metadata == {"source": "user_message", PRIVATE_NOTE_KEY: True}
    assert run_is_private(metadata)


@pytest.mark.parametrize(
    "message_metadata",
    [None, {}, {PRIVATE_NOTE_KEY: False}, {PRIVATE_NOTE_KEY: "yes"}],
    ids=["no-metadata", "empty", "false", "not-a-bool"],
)
def test_anything_short_of_true_is_an_ordinary_message(message_metadata):
    """Only an explicit ``True`` counts: a stray truthy value from a client that
    did not mean it must not silence the group's answer."""
    assert not is_private_note(message_metadata)
    assert run_metadata_for(message_metadata) == {"source": "user_message"}
    assert not run_is_private(run_metadata_for(message_metadata))


def test_the_history_tells_the_model_the_note_was_for_it_alone():
    note = SimpleNamespace(
        text="Don't mention the discount to Tom.",
        metadata={PRIVATE_NOTE_KEY: True},
    )

    rendered = user_prompt_text(note)

    assert rendered.startswith(PRIVATE_NOTE_LABEL)
    assert "Don't mention the discount to Tom." in rendered


def test_an_ordinary_message_carries_no_such_label():
    message = SimpleNamespace(text="what's open for Thursday?", metadata={})

    assert PRIVATE_NOTE_LABEL not in user_prompt_text(message)


def test_a_note_in_a_persons_own_chat_is_not_kept_from_them():
    """The only other reader of a direct chat is the person who wrote the note."""
    from app.modules.agent.domain.private_notes import (
        PRIVATE_NOTE_IN_DM_LABEL,
        note_label,
    )

    assert note_label({"private_note": True, "private_note_in_dm": True}) == (
        PRIVATE_NOTE_IN_DM_LABEL
    )
    assert "reveal" not in PRIVATE_NOTE_IN_DM_LABEL
    assert note_label({"private_note": True}) == PRIVATE_NOTE_LABEL
    assert note_label({}) is None
