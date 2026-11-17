"""What a person said they do, as their preferences keep it."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from app.modules.identity.domain.user_preferences import (
    Job,
    UserPreferences,
    WorkAnswer,
)

pytestmark = pytest.mark.unit

_ASKED = datetime(2030, 1, 2, 3, 4, 5, tzinfo=timezone.utc)


def test_each_job_is_kept_once_in_the_order_given() -> None:
    answer = WorkAnswer(
        jobs=[Job.SALES, Job.FOUNDER, Job.SALES, Job.HR], answered_at=_ASKED
    )
    assert answer.jobs == [Job.SALES, Job.FOUNDER, Job.HR]


@pytest.mark.parametrize("typed,kept", [("  Podcasts ", "Podcasts"), ("   ", None)])
def test_what_they_typed_is_trimmed_and_blank_is_nothing(typed, kept) -> None:
    assert WorkAnswer(other=typed, answered_at=_ASKED).other == kept


def test_something_else_is_bounded() -> None:
    WorkAnswer(other="x" * 120, answered_at=_ASKED)
    with pytest.raises(ValidationError):
        WorkAnswer(other="x" * 121, answered_at=_ASKED)


def test_the_jobs_are_the_ones_the_question_offers() -> None:
    assert [job.value for job in Job] == [
        "SALES",
        "MARKETING",
        "PRODUCT",
        "FOUNDER",
        "OPERATIONS",
        "HR",
        "PROJECT_MANAGEMENT",
        "ENGINEERING",
        "SUPPORT",
    ]


def test_an_answer_survives_a_round_trip_through_storage() -> None:
    """Every writer of preferences round-trips the model, so a key it did not
    declare would be dropped by the next default-surface write."""
    stored = UserPreferences(
        work=WorkAnswer(jobs=[Job.PRODUCT], other="Design", answered_at=_ASKED)
    ).model_dump(mode="json")
    read = UserPreferences.model_validate(stored)
    assert read.work is not None
    assert read.work.jobs == [Job.PRODUCT]
    assert read.work.other == "Design"
    assert read.work.answered_at == _ASKED


def test_an_answer_stored_under_an_older_shape_reads_as_unanswered() -> None:
    """A retired job must not make the whole account unreadable."""
    read = UserPreferences.model_validate(
        {
            "default_surfaces": {},
            "work": {"jobs": ["ASTRONAUT"], "answered_at": _ASKED.isoformat()},
        }
    )
    assert read.work is None
