"""A table change runs as the person who owns the row (audit H16, both ways).

Running every change as the schedule's owner handed anyone who can write a
row the owner's access, through the row's text; running as the row's owner
with an instruction anyone could write handed a schedule's author the
writer's. So a change runs as its row's owner, and only someone who may edit
the target writes a schedule that does.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.services.schedule_ownership import (
    require_target_edit_for_row_owner_runs,
)
from app.modules.schedule.services.trigger_fire import run_person

OWNER = UUID(int=1)
WRITER = UUID(int=2)


class _Ctx:
    """Records what it is asked, refusing ``refuse``."""

    def __init__(self, refuse: frozenset[str] = frozenset()) -> None:
        self.refuse = refuse
        self.asked: list[str] = []

    async def require(self, permission_id: str, resource=None) -> None:
        self.asked.append(permission_id)
        if permission_id in self.refuse:
            raise DomainError("denied", code="PERMISSION_DENIED", status_code=403)


def _schedule(**overrides: object) -> ScheduleEntity:
    values: dict[str, object] = {
        "id": uuid4(),
        "user_id": OWNER,
        "pod_id": uuid4(),
        "agent_id": uuid4(),
        "schedule_type": ScheduleType.DATASTORE,
        "config": {"table_name": "tasks", "operations": ["INSERT"]},
        "instruction": "Triage the task.",
        "visibility": "POD",
    }
    values.update(overrides)
    return ScheduleEntity(**values)


def test_a_change_runs_as_its_rows_owner():
    assert run_person(_schedule(), WRITER) == WRITER


def test_a_row_nobody_owns_runs_as_the_schedule_owner():
    assert run_person(_schedule(), None) == OWNER


def test_a_schedule_set_to_its_owner_runs_every_change_as_them():
    schedule = _schedule(
        config={
            "table_name": "tasks",
            "operations": ["INSERT"],
            "run_as": "SCHEDULE_OWNER",
        }
    )
    assert run_person(schedule, WRITER) == OWNER


def test_time_and_webhook_fires_run_as_the_owner():
    time = _schedule(schedule_type=ScheduleType.TIME, config={"cron": "0 9 * * *"})
    assert run_person(time, WRITER) == OWNER


@pytest.mark.asyncio
async def test_writing_one_takes_edit_access_to_the_agent():
    """Running the target is not enough: the instruction runs with whoever
    owns the next row's access, which is what editing the agent decides."""
    ctx = _Ctx(refuse=frozenset({Permissions.AGENT_UPDATE}))

    with pytest.raises(DomainError):
        await require_target_edit_for_row_owner_runs(_schedule(), ctx)

    assert ctx.asked == [Permissions.AGENT_UPDATE]


@pytest.mark.asyncio
async def test_the_pods_assistant_is_edited_through_the_pod():
    schedule = _schedule()
    schedule = schedule.model_copy(update={"agent_id": schedule.pod_id})
    ctx = _Ctx()

    await require_target_edit_for_row_owner_runs(schedule, ctx)

    assert ctx.asked == [Permissions.POD_UPDATE]


@pytest.mark.asyncio
async def test_a_workflow_target_takes_edit_access_to_the_workflow():
    ctx = _Ctx()

    await require_target_edit_for_row_owner_runs(
        _schedule(agent_id=None, workflow_id=uuid4()), ctx
    )

    assert ctx.asked == [Permissions.WORKFLOW_UPDATE]


@pytest.mark.asyncio
async def test_a_schedule_running_as_its_owner_asks_nothing_more():
    ctx = _Ctx()
    schedule = _schedule(
        config={
            "table_name": "tasks",
            "operations": ["INSERT"],
            "run_as": "SCHEDULE_OWNER",
        }
    )

    await require_target_edit_for_row_owner_runs(schedule, ctx)

    assert ctx.asked == []
