"""How a pause is named and summed up in a "waiting on you" list."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.agent.api.controllers.conversation_pending_controller import (
    encode_pending_page_token,
    parse_pending_page_token,
)
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.pending_interactions import (
    SUMMARY_CHARACTERS,
    PendingInteractionCursor,
    PendingInteractionKind,
    clipped_summary,
)


@pytest.mark.parametrize(
    "pause, kind",
    [
        (PauseKind.QUESTION, PendingInteractionKind.QUESTION),
        (PauseKind.APPROVAL, PendingInteractionKind.APPROVAL),
        (PauseKind.BUDGET, PendingInteractionKind.APPROVAL),
        (PauseKind.HOST_PERMISSION, PendingInteractionKind.PERMISSION),
    ],
)
def test_a_pause_is_named_by_what_it_asks_for(
    pause: PauseKind, kind: PendingInteractionKind
) -> None:
    assert PendingInteractionKind.of(pause) is kind


def test_a_summary_is_one_line() -> None:
    assert clipped_summary("Which region\n  should it cover?") == (
        "Which region should it cover?"
    )


def test_a_long_summary_is_cut_to_fit_with_an_ellipsis() -> None:
    summary = clipped_summary("word " * 100)
    assert summary is not None
    assert len(summary) <= SUMMARY_CHARACTERS
    assert summary.endswith("…")


@pytest.mark.parametrize("blank", [None, "", "   \n"])
def test_nothing_to_say_is_no_summary(blank: str | None) -> None:
    assert clipped_summary(blank) is None


def test_the_page_token_carries_both_sort_keys() -> None:
    cursor = PendingInteractionCursor(
        created_at=datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc),
        message_id=uuid4(),
    )
    assert parse_pending_page_token(encode_pending_page_token(cursor)) == cursor
