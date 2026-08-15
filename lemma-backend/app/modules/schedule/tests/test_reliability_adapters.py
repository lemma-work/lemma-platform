"""Schedule reliability: the dedup key, and the durable publisher.

What used to live here alongside these was a large suite over APScheduler's
reconcile pass and its sync job-store URL. Both are gone with the scheduler; the
properties they protected are now covered against real Postgres in
``tests/e2e/test_due_schedule_claimer_e2e.py`` and
``tests/e2e/test_due_timer_claimer_e2e.py``, where row locking is the mechanism
and a mock could not test it.

These three stay because they are about the event, not the scheduler: the
`source_event_id` is what makes a double fire collapse into one run, and it has
to stay canonical no matter what is doing the firing.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.handlers.schedule_consumer import handle_llm_filter_task
from app.modules.schedule.infrastructure.adapters.schedule_event_publisher import (
    DurableScheduleEventPublisher,
)

@pytest.mark.asyncio
async def test_llm_filter_task_requires_stable_source_event_id() -> None:
    with pytest.raises(ValueError, match="schedule_id is required"):
        await handle_llm_filter_task({}, {})

    with pytest.raises(ValueError, match="source_event_id is required"):
        await handle_llm_filter_task({}, {}, schedule_id=str(uuid4()))

def test_schedule_fired_requires_canonical_source_event_id() -> None:
    with pytest.raises(ValidationError):
        ScheduleFired.model_validate(
            {
                "schedule_id": str(uuid4()),
                "user_id": str(uuid4()),
                "schedule_type": "TIME",
                "payload": {},
            }
        )

@pytest.mark.asyncio
async def test_durable_schedule_publisher_stages_versioned_event(monkeypatch) -> None:
    publish = AsyncMock()
    monkeypatch.setattr(
        "app.modules.schedule.infrastructure.adapters.schedule_event_publisher.EventPublisher.publish",
        publish,
    )
    schedule = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        account_id=None,
        pod_id=uuid4(),
    )

    # The publisher stages whichever owner the caller resolved; it must not
    # quietly substitute the schedule owner for it.
    row_owner = uuid4()
    assert row_owner != schedule.user_id

    await DurableScheduleEventPublisher().publish_schedule_fired(
        schedule,
        {"message": "run"},
        user_id=row_owner,
        metadata={"source": "cron"},
        source_event_id="cron:2026-07-10T00:00:00Z",
    )

    stream, event = publish.await_args.args
    assert stream == "schedule_events"
    assert event.schedule_id == schedule.id
    assert event.user_id == row_owner
    assert event.source_event_id == "cron:2026-07-10T00:00:00Z"
