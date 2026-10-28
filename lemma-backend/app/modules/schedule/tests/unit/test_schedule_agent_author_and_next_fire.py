"""An agent cleans up its own schedule; a schedule says when it next fires.

An agent that created a schedule could not delete it -- ``schedule.delete`` is
destructive and needs a person's approval -- so a first task left a test
schedule and an approval request in a brand-new pod. And nothing said which
zone a cron is read in, so the agent made a schedule and slept to find out.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest

from app.core.authorization.actor import Workload, WorkloadKind
from app.modules.schedule.api.controllers.schedule_controller import (
    _acting_workload_id,
    _deleting_own_agent_schedule,
)
from app.modules.schedule.api.schemas.schedule_schemas import ScheduleResponse
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType

pytestmark = pytest.mark.unit


@dataclass
class _Ctx:
    """The four things the rule reads from an authorization context."""

    is_assistant: bool = False
    workload: Workload | None = None
    delegating_user_id: UUID | None = None

    @property
    def is_named_workload(self) -> bool:
        return self.workload is not None and self.workload.for_user_id is not None


def _agent(agent_id: UUID, user_id: UUID) -> _Ctx:
    return _Ctx(
        workload=Workload(
            kind=WorkloadKind.AGENT, workload_id=agent_id, for_user_id=user_id
        ),
        delegating_user_id=user_id,
    )


def _schedule(*, user_id: UUID, created_by: UUID | None) -> ScheduleEntity:
    return ScheduleEntity(
        user_id=user_id,
        pod_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        config={"cron": "0 9 * * *"},
        created_by_workload_id=created_by,
    )


def test_a_person_is_no_agent():
    assert _acting_workload_id(_Ctx(), uuid4()) is None  # type: ignore[arg-type]


def test_the_assistant_is_known_by_its_pod():
    pod_id = uuid4()
    ctx = _Ctx(is_assistant=True, delegating_user_id=uuid4())
    assert _acting_workload_id(ctx, pod_id) == pod_id  # type: ignore[arg-type]


def test_an_agent_may_delete_what_it_created_for_the_same_person():
    agent_id, user_id, pod_id = uuid4(), uuid4(), uuid4()
    schedule = _schedule(user_id=user_id, created_by=agent_id)
    assert _deleting_own_agent_schedule(_agent(agent_id, user_id), schedule, pod_id)  # type: ignore[arg-type]


def test_the_assistant_may_delete_what_it_created():
    user_id, pod_id = uuid4(), uuid4()
    ctx = _Ctx(is_assistant=True, delegating_user_id=user_id)
    schedule = _schedule(user_id=user_id, created_by=pod_id)
    assert _deleting_own_agent_schedule(ctx, schedule, pod_id)  # type: ignore[arg-type]


@pytest.mark.parametrize("case", ["other_agent", "other_person", "person_made_it"])
def test_everything_else_keeps_the_destructive_gate(case):
    agent_id, user_id, pod_id = uuid4(), uuid4(), uuid4()
    schedule = _schedule(
        user_id=uuid4() if case == "other_person" else user_id,
        created_by=None
        if case == "person_made_it"
        else uuid4()
        if case == "other_agent"
        else agent_id,
    )
    assert not _deleting_own_agent_schedule(
        _agent(agent_id, user_id),  # type: ignore[arg-type]
        schedule,
        pod_id,
    )


def _response(**overrides) -> ScheduleResponse:
    base = {
        "id": uuid4(),
        "user_id": uuid4(),
        "pod_id": uuid4(),
        "name": "digest",
        "schedule_type": ScheduleType.TIME,
        "agent_id": None,
        "workflow_id": None,
        "config": {"cron": "0 9 * * *"},
        "connector_trigger_id": None,
        "filter_instruction": None,
        "filter_output_schema": None,
        "visibility": "POD",
        "is_active": True,
        "is_internal": False,
        "created_at": datetime.now(timezone.utc),
        "updated_at": datetime.now(timezone.utc),
    }
    base.update(overrides)
    return ScheduleResponse(**base)


def test_next_fire_is_also_read_in_the_schedules_zone():
    fire = datetime(2026, 7, 1, 7, 0, tzinfo=timezone.utc)
    response = _response(
        config={"cron": "0 9 * * *", "timezone": "Europe/Berlin"}, next_fire_at=fire
    )
    dumped = response.model_dump(mode="json")
    assert dumped["next_fire_at"].startswith("2026-07-01T07:00:00")
    assert dumped["next_fire_at_local"] == "2026-07-01T09:00:00+02:00"


def test_without_a_zone_the_local_time_is_utc():
    fire = datetime(2026, 7, 1, 9, 0, tzinfo=timezone.utc)
    assert _response(next_fire_at=fire).next_fire_at_local == (
        "2026-07-01T09:00:00+00:00"
    )


def test_nothing_due_says_nothing():
    assert _response().next_fire_at_local is None
