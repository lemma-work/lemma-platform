"""PS-SCHED-023: the system never deactivates a schedule silently.

Two sites in the poller set ``is_active = False`` directly -- a cron the
library can no longer produce a fire for, and a one-shot whose moment passed
while nothing was polling. Both recorded it in a log line and nothing else, so
an automation could be retired and its owner would find an inactive row with no
explanation anywhere. The paused-schedule email has carried copy for both
reasons all along; only the event was missing.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.schedule.domain.events.schedule import ScheduleDeactivated
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.services.due_schedule_claimer import (
    backfill_missing_cursors,
    claim_due_schedules,
)

pytestmark = pytest.mark.asyncio


class _CollectingUow:
    """Stands in for the poller's unit of work: it collects, it does not send."""

    def __init__(self) -> None:
        self.events: list[object] = []

    def collect_events(self, events) -> None:
        self.events.extend(events)


class _OneRowSession:
    """Answers the claimer's single SELECT with one row."""

    def __init__(self, row: SimpleNamespace) -> None:
        self._row = row

    async def scalars(self, _statement):
        row = self._row

        class _Result:
            def all(self):
                return [row]

        return _Result()


def _row(**overrides) -> SimpleNamespace:
    """A schedule row as the claimer sees it.

    A stand-in rather than the ORM model: the claimer reads and writes plain
    attributes, and mapping the real class here would pull in every model it
    has a relationship to for no gain in what is being asserted.
    """
    values = {
        "id": uuid4(),
        "user_id": uuid4(),
        "pod_id": uuid4(),
        "name": "nightly",
        "schedule_type": ScheduleType.TIME,
        "config": {},
        "is_active": True,
        "consecutive_failures": 0,
        "next_fire_at": None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _deactivations(uow: _CollectingUow) -> list[ScheduleDeactivated]:
    return [e for e in uow.events if isinstance(e, ScheduleDeactivated)]


async def test_a_missed_one_shot_says_so_when_it_is_retired() -> None:
    now = datetime.now(timezone.utc)
    row = _row(config={"scheduled_at": (now - timedelta(hours=5)).isoformat()})
    uow = _CollectingUow()

    await backfill_missing_cursors(_OneRowSession(row), now=now, uow=uow)

    assert row.is_active is False
    deactivated = _deactivations(uow)
    assert len(deactivated) == 1
    assert deactivated[0].reason == "SCHEDULE_ONE_TIME_MISSED"
    assert deactivated[0].schedule_id == row.id
    assert deactivated[0].user_id == row.user_id


async def test_an_unusable_expression_says_so_when_it_is_retired() -> None:
    now = datetime.now(timezone.utc)
    row = _row(config={"cron": "not a cron"})
    uow = _CollectingUow()

    await backfill_missing_cursors(_OneRowSession(row), now=now, uow=uow)

    assert row.is_active is False
    assert [e.reason for e in _deactivations(uow)] == ["SCHEDULE_VALIDATION_ERROR"]


async def test_a_one_shot_that_simply_ran_is_retired_quietly() -> None:
    """Retirement is not a fault: telling the owner it was "paused" is wrong.

    A one-shot going inactive after it fires is the whole point of a one-shot.
    """
    now = datetime.now(timezone.utc)
    fire_at = now - timedelta(seconds=1)
    row = _row(
        config={"scheduled_at": fire_at.isoformat()},
        next_fire_at=fire_at,
    )
    uow = _CollectingUow()

    claimed = await claim_due_schedules(_OneRowSession(row), now=now, uow=uow)

    assert len(claimed) == 1
    assert row.is_active is False
    assert _deactivations(uow) == []
