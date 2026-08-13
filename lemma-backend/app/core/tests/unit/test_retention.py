"""The retention rule: what a sweep is allowed to delete."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

import pytest

from app.core.retention import RetentionPolicy, select_prunable

pytestmark = pytest.mark.unit

NOW = datetime(2026, 8, 13, tzinfo=timezone.utc)


@dataclass
class _Version:
    id: UUID
    created_at: datetime
    pruned_at: datetime | None = None
    name: str = ""


def _history(ages_in_days: list[int]) -> list[_Version]:
    """Newest first: ``ages[0]`` is the newest build.

    Entries the same number of days old are separated by a second, because real
    deploys are: a burst of them shares a day, not an instant. Ids are uuid7 as
    in both real tables, so the tie-break on id is creation order rather than
    random -- which is exactly what stops a same-tick tie from ranking a newer
    build below an older one.
    """
    ordered = sorted(range(len(ages_in_days)), key=lambda index: -ages_in_days[index])
    versions: dict[int, _Version] = {}
    for index in ordered:
        versions[index] = _Version(
            id=uuid7(),
            created_at=NOW - timedelta(days=ages_in_days[index], seconds=index),
            name=f"v{index}",
        )
    return [versions[index] for index in range(len(ages_in_days))]


def _prune(versions, *, live=None, **policy_kwargs):
    policy = RetentionPolicy(**policy_kwargs)
    return select_prunable(versions, policy=policy, live_id=live, now=NOW)


def test_the_live_version_is_never_prunable_however_old_or_deep():
    """Deleting the bytes something is currently serving is the one outcome
    retention must never produce."""
    history = _history([1] + [500] * 39)
    live = history[39]  # the oldest entry, rank 40, promoted back to live

    prunable = _prune(history, live=live.id)

    assert live not in prunable
    # 40 entries: the floor keeps ranks 1-10, leaving 30 candidates, of which
    # the live one is exempt.
    assert len(prunable) == 29


def test_the_floor_keeps_the_ten_newest_at_any_age():
    """An app nobody has deployed in years must still be rollback-able the day a
    bad deploy lands, so age alone cannot be the rule."""
    history = _history([400 + index for index in range(12)])

    prunable = _prune(history, live=history[0].id)

    assert [version.name for version in prunable] == ["v10", "v11"]


def test_the_ceiling_bounds_a_burst_of_deploys():
    """Fifty deploys in one afternoon are all 'recent'. Without a ceiling that
    means fifty retained builds for a month, which is the unbounded case."""
    history = _history([0] * 50)

    prunable = _prune(history, live=history[0].id)

    assert len(prunable) == 30  # the newest 20 survive
    assert {version.name for version in prunable} == {
        f"v{index}" for index in range(20, 50)
    }


def test_between_floor_and_ceiling_age_decides():
    # Ranks 11-20 are past the floor but under the ceiling: young ones stay.
    history = _history([0] * 10 + [1, 2, 3] + [40, 50])

    prunable = _prune(history, live=history[0].id)

    assert [version.name for version in prunable] == ["v13", "v14"]


def test_an_already_pruned_entry_is_not_selected_again():
    """Pruning is idempotent -- re-selecting these would re-delete objects that
    are already gone on every tick."""
    history = _history([100] * 15)
    history[12].pruned_at = NOW

    prunable = _prune(history, live=history[0].id)

    assert history[12] not in prunable
    assert [version.name for version in prunable] == ["v10", "v11", "v13", "v14"]


def test_nothing_is_prunable_below_the_floor():
    history = _history([900] * 10)

    assert _prune(history, live=history[0].id) == []


def test_ranking_does_not_depend_on_input_order():
    history = _history([400 + index for index in range(12)])
    shuffled = [history[5], history[11], history[0], *history[1:5], *history[6:11]]

    assert {version.name for version in _prune(shuffled, live=history[0].id)} == {
        "v10",
        "v11",
    }


def test_a_ceiling_below_the_floor_is_refused():
    """It would silently delete inside the range the floor promises to keep."""
    with pytest.raises(ValueError, match="max_keep"):
        RetentionPolicy(keep_last=10, max_keep=5)
