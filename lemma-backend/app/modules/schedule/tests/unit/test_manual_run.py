"""Which schedules a person may run by hand, and what such a run fires with.

Only a TIME schedule: the others' runs carry the delivery or the record that
fired them, and a run by hand has none. A paused schedule stays paused for a
run by hand too, and one whose target was deleted has nothing to run.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.modules.schedule.domain.errors import ScheduleNotRunnableNowError
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.services.manual_run import (
    CODE_INACTIVE,
    CODE_NEEDS_EVENT,
    CODE_TARGET_MISSING,
    MANUAL_SOURCE_PREFIX,
    manual_trigger_event,
    refuse_manual_run,
    require_may_run_target,
)
from app.modules.schedule.work.kinds import time_event_payload

OWNER = UUID(int=1)
PERSON = UUID(int=2)


def _schedule(**overrides: object) -> ScheduleEntity:
    values: dict[str, object] = {
        "id": uuid4(),
        "user_id": OWNER,
        "pod_id": uuid4(),
        "agent_id": uuid4(),
        "schedule_type": ScheduleType.TIME,
        "config": {"cron": "0 9 * * *"},
        "instruction": "Write the morning summary.",
    }
    values.update(overrides)
    return ScheduleEntity(**values)


def _refusal(schedule: ScheduleEntity) -> ScheduleNotRunnableNowError:
    with pytest.raises(ScheduleNotRunnableNowError) as refused:
        refuse_manual_run(schedule)
    return refused.value


def test_an_active_time_schedule_with_a_target_may_be_run_by_hand():
    assert refuse_manual_run(_schedule()) is None


@pytest.mark.parametrize(
    ("schedule_type", "config", "carried"),
    [
        (ScheduleType.WEBHOOK, {"source": "custom"}, "webhook delivery"),
        (
            ScheduleType.DATASTORE,
            {"table_name": "tasks", "operations": ["INSERT"]},
            "changed record",
        ),
    ],
)
def test_a_schedule_whose_run_carries_an_event_is_refused_as_unrunnable(
    schedule_type: ScheduleType, config: dict[str, object], carried: str
):
    refusal = _refusal(_schedule(schedule_type=schedule_type, config=config))

    assert (refusal.status_code, refusal.code) == (422, CODE_NEEDS_EVENT)
    assert carried in refusal.message


def test_a_paused_event_schedule_is_told_it_can_never_run_rather_than_to_resume():
    """Resuming would not help it, so the pause is not what it is told."""
    refusal = _refusal(
        _schedule(
            schedule_type=ScheduleType.WEBHOOK,
            config={"source": "custom"},
            is_active=False,
        )
    )

    assert refusal.code == CODE_NEEDS_EVENT


def test_a_paused_schedule_is_refused_until_resumed():
    refusal = _refusal(_schedule(is_active=False))

    assert (refusal.status_code, refusal.code) == (409, CODE_INACTIVE)
    assert "Resume it" in refusal.message


def test_a_schedule_the_platform_paused_says_why():
    refusal = _refusal(_schedule(is_active=False, paused_reason="fire_cap"))

    assert "fire_cap" in refusal.message


def test_a_schedule_whose_target_was_deleted_has_nothing_to_run():
    refusal = _refusal(_schedule(agent_id=None))

    assert (refusal.status_code, refusal.code) == (409, CODE_TARGET_MISSING)


def test_a_run_by_hand_carries_what_the_timer_would_and_who_asked():
    schedule = _schedule(config={"cron": "0 9 * * *", "payload": {"report": "daily"}})

    event = manual_trigger_event(schedule, person=PERSON)

    assert event.payload == {"report": "daily"}
    assert event.payload == time_event_payload(schedule.config)
    assert event.metadata == {"manual": True, "started_by_user_id": str(PERSON)}
    assert event.started_by_user_id == PERSON
    assert event.row_owner_user_id is None


def test_every_run_by_hand_is_its_own_event():
    schedule = _schedule()

    first = manual_trigger_event(schedule, person=PERSON)
    second = manual_trigger_event(schedule, person=PERSON)

    assert first.source_event_id.startswith(MANUAL_SOURCE_PREFIX)
    assert first.source_event_id != second.source_event_id


@pytest.mark.parametrize("payload", [None, "not an object", ["a", "list"]])
def test_a_time_payload_that_is_not_an_object_carries_nothing(payload: object):
    assert time_event_payload({"cron": "0 9 * * *", "payload": payload}) == {}


class _Ctx:
    """Records what it is asked, refusing ``refuse``."""

    def __init__(self, refuse: frozenset[str] = frozenset()) -> None:
        self.refuse = refuse
        self.asked: list[str] = []

    async def require(self, permission_id: str, resource=None) -> None:
        del resource
        self.asked.append(permission_id)
        if permission_id in self.refuse:
            raise DomainError("denied", code="PERMISSION_DENIED", status_code=403)


@pytest.mark.asyncio
async def test_a_person_who_may_not_run_the_agent_may_not_run_its_schedule():
    schedule = _schedule()
    ctx = _Ctx(refuse=frozenset({Permissions.AGENT_EXECUTE}))

    with pytest.raises(DomainError) as refused:
        await require_may_run_target(ctx, schedule, pod_id=uuid4())  # type: ignore[arg-type]

    assert refused.value.status_code == 403


@pytest.mark.asyncio
async def test_a_workflow_schedule_asks_whether_the_person_may_run_the_workflow():
    schedule = _schedule(agent_id=None, workflow_id=uuid4())
    ctx = _Ctx(refuse=frozenset({Permissions.WORKFLOW_EXECUTE}))

    with pytest.raises(DomainError) as refused:
        await require_may_run_target(ctx, schedule, pod_id=uuid4())  # type: ignore[arg-type]

    assert refused.value.status_code == 403
    assert ctx.asked == [Permissions.WORKFLOW_EXECUTE]
