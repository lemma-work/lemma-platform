"""A time schedule fires on the clock: there is no event for a filter to judge.

It used to accept a filter and never ask it. A new one is refused now; one saved
before keeps loading, can be cleared, and can be sent back unchanged by a client
that edits the cron and resubmits the whole form.
"""

from __future__ import annotations

import pytest

from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.services.time_schedule_policy import (
    refuse_new_time_schedule_filter,
    refuse_time_schedule_filter,
)

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("instruction", "schema"),
    [("Only weekdays", None), (None, {"type": "object"}), ("  x  ", {})],
)
def test_a_new_time_schedule_with_a_filter_is_refused(instruction, schema) -> None:
    with pytest.raises(ScheduleValidationError) as raised:
        refuse_time_schedule_filter(instruction, schema)

    assert raised.value.status_code == 422


@pytest.mark.parametrize(
    ("instruction", "schema"), [(None, None), ("", {}), ("  ", None)]
)
def test_a_new_time_schedule_without_one_is_fine(instruction, schema) -> None:
    refuse_time_schedule_filter(instruction, schema)


def test_an_old_filter_resent_unchanged_is_accepted() -> None:
    refuse_new_time_schedule_filter(
        "Only weekdays",
        {"type": "object"},
        {
            "config": {"cron": "0 9 * * *"},
            "filter_instruction": "Only weekdays",
            "filter_output_schema": {"type": "object"},
        },
    )


def test_an_update_that_does_not_mention_the_filter_is_accepted() -> None:
    refuse_new_time_schedule_filter("Only weekdays", None, {"name": "renamed"})


@pytest.mark.parametrize("cleared", ["", "   ", None])
def test_clearing_the_instruction_clears_the_filter(cleared) -> None:
    refuse_new_time_schedule_filter(
        "Only weekdays", {"type": "object"}, {"filter_instruction": cleared}
    )


def test_a_changed_or_new_filter_is_refused() -> None:
    with pytest.raises(ScheduleValidationError):
        refuse_new_time_schedule_filter(
            "Only weekdays", None, {"filter_instruction": "Only weekends"}
        )
    with pytest.raises(ScheduleValidationError):
        refuse_new_time_schedule_filter(None, None, {"filter_instruction": "Only VIPs"})
