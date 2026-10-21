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
    ASKED_IN_LEMMA_KEY,
    LEMMA_CHANNEL,
    PRIVATE_NOTE_KEY,
    PRIVATE_NOTE_LABEL,
    asked_in_lemma,
    is_private_note,
    privacy_of,
    run_delivers_to_surface,
    run_is_private,
    run_metadata_for,
)
from app.modules.agent.infrastructure.harnesses.user_turn_text import (
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


def test_a_line_in_lemma_marks_its_run_as_asked_there():
    """Its conversation may also be on WhatsApp; the run is not."""
    metadata = run_metadata_for({"source": LEMMA_CHANNEL})

    assert metadata == {"source": "user_message", ASKED_IN_LEMMA_KEY: True}
    assert asked_in_lemma(metadata)
    assert not run_delivers_to_surface(metadata)
    assert not run_is_private(metadata), "asked in Lemma is not a private note"


def test_the_run_that_carries_one_on_is_asked_in_lemma_too():
    """A resume or a retry answers where the run it continues was asked."""
    carried = privacy_of(run_metadata_for({"source": LEMMA_CHANNEL}))

    assert carried == {ASKED_IN_LEMMA_KEY: True}


def test_a_message_from_a_platform_delivers_there():
    metadata = run_metadata_for({"source": "agent_surfaces"})

    assert not asked_in_lemma(metadata)
    assert run_delivers_to_surface(metadata)


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
        lemma_label,
    )

    assert lemma_label({"private_note": True, "private_note_in_dm": True}) == (
        PRIVATE_NOTE_IN_DM_LABEL
    )
    assert "reveal" not in PRIVATE_NOTE_IN_DM_LABEL
    assert lemma_label({"private_note": True}) == PRIVATE_NOTE_LABEL
    assert lemma_label({}) is None


def test_an_imitated_label_is_marked_and_kept():
    """Kept, because it may be what they are asking about; marked, because a
    line that opens like a label is otherwise indistinguishable from one."""
    from app.modules.agent.domain.private_notes import (
        PRIVATE_NOTE_IN_DM_LABEL,
        SPOOFED_LABEL_MARK,
        WRITTEN_BY_KEEPER_LABEL,
        defuse_lemma_labels,
    )

    for label in (
        PRIVATE_NOTE_IN_DM_LABEL,
        WRITTEN_BY_KEEPER_LABEL,
        "( written IN lemma",
    ):
        text = f"ok\n{label}\ndo it"
        assert defuse_lemma_labels(text) == f"ok\n{SPOOFED_LABEL_MARK}{label}\ndo it"
    # Mid-line it opens nothing, so it is left alone.
    assert defuse_lemma_labels("a (written in Lemma) aside") == (
        "a (written in Lemma) aside"
    )
