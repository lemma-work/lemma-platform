"""What one digest sends: oldest first, inside its bounds, one run per owner."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.schedule.domain.schedule import ScheduleRunEntity, ScheduleRunStatus
from app.modules.schedule.domain.triage import TriageRoute
from app.modules.schedule.services.digest_dispatcher import (
    DIGEST_EVENT_MAX_CHARS,
    DIGEST_MAX_CHARS,
    DIGEST_MAX_EVENTS,
    digest_event,
    digest_event_id,
    plan_digest,
)

pytestmark = pytest.mark.unit

START = datetime(2026, 10, 1, 8, 0, tzinfo=timezone.utc)
SCHEDULE = uuid4()


def _held(
    index: int, *, owner: UUID | None, payload: dict[str, object] | None = None
) -> ScheduleRunEntity:
    return ScheduleRunEntity(
        schedule_id=SCHEDULE,
        user_id=owner,
        source_event_id=f"event-{index}",
        status=ScheduleRunStatus.HELD,
        target_kind="WORKFLOW",
        target_run_id=None,
        payload=payload if payload is not None else {"n": index},
        held_for=TriageRoute.DIGEST,
        created_at=START + timedelta(minutes=index),
    )


def test_one_owner_is_one_digest_of_every_held_event_oldest_first():
    owner = uuid4()
    held = [_held(index, owner=owner) for index in range(3)]

    plan = plan_digest(held)

    [batch] = plan.batches
    assert batch.user_id == owner
    assert batch.events == [{"n": 0}, {"n": 1}, {"n": 2}]
    assert [run.source_event_id for run in batch.runs] == [
        "event-0",
        "event-1",
        "event-2",
    ]
    assert plan.more_waiting is False


def test_each_owner_of_an_rls_row_gets_a_digest_of_their_own():
    first, second = uuid4(), uuid4()
    held = [
        _held(0, owner=first),
        _held(1, owner=second),
        _held(2, owner=first),
    ]

    plan = plan_digest(held)

    by_owner = {batch.user_id: batch.events for batch in plan.batches}
    assert by_owner == {first: [{"n": 0}, {"n": 2}], second: [{"n": 1}]}


def test_past_the_count_the_rest_wait_for_the_next_digest():
    owner = uuid4()
    held = [_held(index, owner=owner) for index in range(DIGEST_MAX_EVENTS + 1)]

    plan = plan_digest(held)

    [batch] = plan.batches
    assert len(batch.events) == DIGEST_MAX_EVENTS
    assert plan.more_waiting is True


def test_past_the_size_the_rest_wait_and_order_is_kept():
    owner = uuid4()
    chunk = "x" * (DIGEST_EVENT_MAX_CHARS - 100)
    fits = DIGEST_MAX_CHARS // DIGEST_EVENT_MAX_CHARS
    held = [
        _held(index, owner=owner, payload={"body": chunk}) for index in range(fits + 2)
    ]

    plan = plan_digest(held)

    [batch] = plan.batches
    assert len(batch.runs) == fits
    assert [run.source_event_id for run in batch.runs] == [
        f"event-{index}" for index in range(fits)
    ]
    assert plan.more_waiting is True


def test_an_event_too_large_to_quote_goes_in_as_a_stub_naming_its_run():
    run = _held(0, owner=uuid4(), payload={"body": "x" * DIGEST_EVENT_MAX_CHARS})

    stub = digest_event(run)

    assert stub == {
        "truncated": True,
        "characters": len('{"body": ""}') + DIGEST_EVENT_MAX_CHARS,
        "run_id": str(run.id),
        "source_event_id": "event-0",
    }
    # And it still goes out: one oversized event must not hold up the rest.
    [batch] = plan_digest([run]).batches
    assert batch.events == [stub]


def test_a_digest_is_filed_under_its_occurrence_and_its_owner():
    owner = uuid4()
    due = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)

    assert digest_event_id(due, owner) == f"digest:2026-10-01T09:00:00+00:00:{owner}"
