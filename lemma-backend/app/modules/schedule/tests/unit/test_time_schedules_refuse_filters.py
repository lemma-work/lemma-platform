"""A TIME schedule has no event for a filter to judge, so it refuses one.

It used to accept `filter_instruction` and ignore it, so a person who wrote
"only on weekdays" believed it was being checked. Create refuses it in the
request schema; update refuses it in the service, which knows the type the
request does not carry.
"""

from __future__ import annotations

from contextlib import nullcontext as does_not_raise
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.core.authorization.context import Context
from app.modules.schedule.api.schemas.schedule_schemas import (
    CreateScheduleRequest,
    UpdateScheduleRequest,
)
from app.modules.schedule.domain.errors import ScheduleValidationError
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.services.schedule_update_policy import (
    validate_schedule_update_policies,
)

pytestmark = pytest.mark.unit


def _create(**fields: object) -> CreateScheduleRequest:
    return CreateScheduleRequest.model_validate(
        {"agent_name": "kit", "instruction": "Post the digest.", **fields}
    )


@pytest.mark.parametrize(
    "condition",
    [
        {"filter_instruction": "Only on weekdays."},
        {"filter_output_schema": {"type": "object"}},
    ],
)
def test_creating_a_time_schedule_with_a_filter_is_refused(
    condition: dict[str, object],
):
    with pytest.raises(ValidationError, match="no event to judge"):
        _create(schedule_type="TIME", config={"cron": "0 9 * * *"}, **condition)


def test_an_empty_filter_on_a_time_schedule_is_no_filter_at_all():
    request = _create(
        schedule_type="TIME", config={"cron": "0 9 * * *"}, filter_instruction=""
    )

    assert request.filter_instruction == ""


@pytest.mark.parametrize(
    "schedule_type, config",
    [
        ("WEBHOOK", {"source": "composio"}),
        ("DATASTORE", {"table_name": "tickets", "operations": ["INSERT"]}),
    ],
)
def test_an_event_schedule_keeps_its_filter(
    schedule_type: str, config: dict[str, object]
):
    request = CreateScheduleRequest.model_validate(
        {
            "schedule_type": schedule_type,
            "config": config,
            "workflow_name": "intake",
            "filter_instruction": "Only urgent tickets.",
        }
    )

    assert request.filter_instruction == "Only urgent tickets."


def test_a_filter_longer_than_a_decision_can_hold_is_refused():
    with pytest.raises(ValidationError):
        UpdateScheduleRequest(filter_instruction="x" * 8001)


def _existing(schedule_type: ScheduleType) -> ScheduleEntity:
    return ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=schedule_type,
        config={"cron": "0 9 * * *"}
        if schedule_type is ScheduleType.TIME
        else {"source": "composio"},
    )


async def _no_datastore_check(schedule: ScheduleEntity, ctx: Context | None) -> None:
    raise AssertionError("only a DATASTORE update asks the table")


async def test_updating_a_time_schedule_to_carry_a_filter_is_refused():
    with pytest.raises(ScheduleValidationError, match="no event to judge"):
        await validate_schedule_update_policies(
            _existing(ScheduleType.TIME),
            {"filter_instruction": "Only on weekdays."},
            ctx=None,
            require_datastore_update=_no_datastore_check,
        )


async def test_a_filter_saved_on_a_time_schedule_before_can_still_be_cleared():
    with does_not_raise():
        await validate_schedule_update_policies(
            _existing(ScheduleType.TIME),
            {"filter_instruction": ""},
            ctx=None,
            require_datastore_update=_no_datastore_check,
        )


async def test_updating_a_webhook_schedules_filter_is_allowed():
    with does_not_raise():
        await validate_schedule_update_policies(
            _existing(ScheduleType.WEBHOOK),
            {"filter_instruction": "Only urgent tickets."},
            ctx=None,
            require_datastore_update=_no_datastore_check,
        )
