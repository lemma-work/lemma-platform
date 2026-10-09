"""A conversation handed to a member: who has it, and until when."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.modules.agent.domain.outsiders import (
    HANDED_TO_KEY,
    handed_to,
    with_audience_kept,
)

pytestmark = pytest.mark.unit

NOW = datetime(2026, 10, 9, 12, tzinfo=timezone.utc)


def _metadata(user_id, until) -> dict[str, object]:
    return {HANDED_TO_KEY: {"user_id": str(user_id), "until": until.isoformat()}}


def test_a_conversation_is_handed_to_the_member_until_the_hand_off_ends():
    member = uuid4()
    metadata = _metadata(member, NOW + timedelta(days=3))

    assert handed_to(metadata, now=NOW) == member
    assert handed_to(metadata, now=NOW + timedelta(days=3)) is None


@pytest.mark.parametrize(
    "metadata",
    [
        None,
        {},
        {HANDED_TO_KEY: "someone"},
        {HANDED_TO_KEY: {"user_id": "not-a-uuid", "until": NOW.isoformat()}},
        {HANDED_TO_KEY: {"user_id": str(uuid4()), "until": "2026-12-01T00:00:00"}},
    ],
    ids=["none", "empty", "not-a-mapping", "bad-member", "no-timezone"],
)
def test_anything_else_is_nobody(metadata):
    assert handed_to(metadata, now=NOW) is None


def test_a_member_hands_it_back_by_dropping_the_key():
    """Not a protected key: replacing the metadata without it hands back."""
    existing = {"audience": "contact", **_metadata(uuid4(), NOW + timedelta(days=1))}

    kept = with_audience_kept(existing, {"title_hint": "x"})

    assert kept is not None
    assert HANDED_TO_KEY not in kept
    assert kept["audience"] == "contact"
