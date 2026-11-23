"""A schedule written with a zone name the tz database has retired.

Browsers still report India as ``Asia/Calcutta``. Debian 13 ships that name only
in ``tzdata-legacy``, so a host on current zone data alone does not have it,
and a schedule that a host with the old link accepted would stop arming there.
Reading the current name is what holds on both kinds of host, so these hold on
whichever one runs them. What a host older than a rename does is pinned on the
helper itself, in ``app/core/tests/unit/test_renamed_time_zones.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.helpers.time_zones import RENAMED_ZONES
from app.modules.schedule.domain.cron import CronSchedule, resolve_zone


@pytest.mark.parametrize(("given", "current"), sorted(RENAMED_ZONES.items()))
def test_a_retired_name_is_read_as_its_current_one(given: str, current: str) -> None:
    assert str(resolve_zone(given)) == current


def test_an_unknown_name_is_still_refused() -> None:
    with pytest.raises(ValueError, match="Unknown time zone"):
        resolve_zone("Asia/Nowhere")


def test_the_schedule_keeps_the_name_it_was_given_and_fires_in_that_zone() -> None:
    """Stored as written, so an exported bundle says what its author said;
    read in India, so nine in the morning is 03:30Z."""
    schedule = CronSchedule.parse("0 9 * * *", zone="Asia/Calcutta")

    assert schedule.zone_name == "Asia/Calcutta"
    assert schedule.next_fire_time(
        datetime(2026, 1, 15, 0, 0, tzinfo=timezone.utc)
    ) == datetime(2026, 1, 15, 3, 30, tzinfo=timezone.utc)
