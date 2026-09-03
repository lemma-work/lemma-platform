"""Telling a breaker pause apart from a deliberate one.

A schedule that stops itself after five consecutive failures and a schedule
somebody paused on purpose both set ``is_active`` false, and nothing else on
the response distinguished them. The pod overview then filters inactive
schedules out entirely, so a schedule the platform stopped simply disappeared.

"The user never sees why their schedules stopped" was that. The failures were
recorded, counted, emailed on deactivation and served on the runs endpoint the
whole time — none of it tied to the pause the user was actually looking at.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.modules.schedule.api.schemas.schedule_schemas import ScheduleResponse
from app.modules.schedule.config import schedule_settings
from app.modules.schedule.domain.schedule import ScheduleType

pytestmark = pytest.mark.unit

_THRESHOLD = schedule_settings.schedule_max_consecutive_failures


def _schedule(**overrides) -> ScheduleResponse:
    base = {
        "id": uuid4(),
        "user_id": uuid4(),
        "pod_id": uuid4(),
        "name": "nightly",
        "schedule_type": ScheduleType.TIME,
        "agent_id": None,
        "workflow_id": None,
        "config": {},
        "account_id": None,
        "connector_trigger_id": None,
        "filter_instruction": None,
        "filter_output_schema": None,
        "visibility": "POD",
        "is_active": False,
        "is_internal": False,
        "consecutive_failures": 0,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return ScheduleResponse(**base)


def test_a_schedule_at_the_threshold_reports_the_breaker_paused_it():
    assert _schedule(consecutive_failures=_THRESHOLD).paused_by_failures is True


def test_a_schedule_paused_by_hand_does_not_blame_the_breaker():
    """Some failures is not enough failures, and pausing a flaky schedule by
    hand is exactly what a user does before it trips."""
    assert _schedule(consecutive_failures=_THRESHOLD - 1).paused_by_failures is False


def test_a_running_schedule_is_never_reported_as_paused():
    schedule = _schedule(is_active=True, consecutive_failures=_THRESHOLD)

    assert schedule.paused_by_failures is False


def test_it_reaches_the_wire_rather_than_only_existing_on_the_model():
    """Computed fields are absent from pydantic's validation-mode schema, so
    the property worth pinning is that it actually serializes."""
    payload = _schedule(consecutive_failures=_THRESHOLD).model_dump()

    assert payload["paused_by_failures"] is True
    assert payload["consecutive_failures"] == _THRESHOLD
    assert (
        "paused_by_failures"
        in ScheduleResponse.model_json_schema(mode="serialization")["properties"]
    ), "absent from the response schema, so no SDK or client will see it"
