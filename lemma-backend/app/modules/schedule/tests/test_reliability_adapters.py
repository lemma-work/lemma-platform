from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.handlers.schedule_consumer import handle_llm_filter_task
from app.modules.schedule.infrastructure.adapters.schedule_event_publisher import (
    DurableScheduleEventPublisher,
)
from app.modules.schedule.scheduler import scheduler_service


@pytest.mark.asyncio
async def test_llm_filter_task_requires_stable_source_event_id() -> None:
    with pytest.raises(ValueError, match="schedule_id is required"):
        await handle_llm_filter_task({}, {})

    with pytest.raises(ValueError, match="source_event_id is required"):
        await handle_llm_filter_task({}, {}, schedule_id=str(uuid4()))


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

    await DurableScheduleEventPublisher().publish_schedule_fired(
        schedule,
        {"message": "run"},
        metadata={"source": "cron"},
        source_event_id="cron:2026-07-10T00:00:00Z",
    )

    stream, event = publish.await_args.args
    assert stream == "schedule_events"
    assert event.schedule_id == schedule.id
    assert event.source_event_id == "cron:2026-07-10T00:00:00Z"


@pytest.mark.asyncio
async def test_scheduler_job_uses_uuid_and_empty_payload(monkeypatch) -> None:
    emitter = SimpleNamespace(emit_scheduled_job_event=AsyncMock())
    monkeypatch.setattr(scheduler_service, "get_event_emitter", lambda: emitter)
    scheduled_at = datetime(2026, 7, 10, tzinfo=timezone.utc)
    monkeypatch.setattr(
        scheduler_service,
        "current_scheduled_run_time",
        lambda: scheduled_at,
    )
    schedule_id = uuid4()

    await scheduler_service.execute_scheduled_job(str(schedule_id))

    emitter.emit_scheduled_job_event.assert_awaited_once_with(
        schedule_id=schedule_id,
        payload={},
        scheduled_at=scheduled_at,
    )
