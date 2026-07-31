from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, call
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.modules.schedule.domain.events.schedule import ScheduleFired
from app.modules.schedule.domain.schedule import ScheduleType
from app.modules.schedule.handlers.schedule_consumer import handle_llm_filter_task
from app.modules.schedule.infrastructure.adapters.schedule_event_publisher import (
    DurableScheduleEventPublisher,
)
from app.modules.schedule.scheduler import events, scheduler_service
from app.modules.schedule.scheduler.api_client import SchedulerAPIClient
from app.composition.workflow_scheduler import ScheduleControlAdapter


@pytest.mark.parametrize(
    ("database_url", "expected_sslmode"),
    [
        (
            "postgresql+asyncpg://lemma:p%40ss@db.example/gappynew?ssl=require&application_name=lemma",
            "require",
        ),
        (
            "postgresql://lemma:secret@db.example/gappynew?sslmode=verify-full",
            "verify-full",
        ),
    ],
)
def test_sync_jobstore_url_uses_psycopg_and_libpq_sslmode(
    database_url: str,
    expected_sslmode: str,
) -> None:
    from sqlalchemy.engine import make_url

    result = make_url(scheduler_service.build_sync_jobstore_url(database_url))

    assert result.drivername == "postgresql+psycopg"
    assert result.password in {"p@ss", "secret"}
    assert result.query["sslmode"] == expected_sslmode
    assert "ssl" not in result.query
    if "application_name" in database_url:
        assert result.query["application_name"] == "lemma"


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

    await DurableScheduleEventPublisher().publish_schedule_fired(
        schedule,
        {"message": "run"},
        metadata={"source": "cron"},
        source_event_id="cron:2026-07-10T00:00:00Z",
    )

    stream, event = publish.await_args.args
    assert stream == "schedule_events"
    assert event.schedule_id == schedule.id
    assert event.user_id == schedule.user_id
    assert event.source_event_id == "cron:2026-07-10T00:00:00Z"


@pytest.mark.asyncio
async def test_time_event_uses_job_owner(monkeypatch) -> None:
    schedule_id = uuid4()
    user_id = uuid4()
    publish = AsyncMock()
    monkeypatch.setattr(events.EventPublisher, "publish", publish)
    emitter = events.SchedulerEventEmitter()
    await emitter.start()

    await emitter.emit_scheduled_job_event(
        schedule_id,
        user_id,
        {"source": "cron"},
        scheduled_at=datetime(2026, 7, 10, tzinfo=timezone.utc),
    )

    _, event = publish.await_args.args
    assert event.user_id == user_id


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
    user_id = uuid4()

    await scheduler_service.execute_scheduled_job(str(schedule_id), str(user_id))

    emitter.emit_scheduled_job_event.assert_awaited_once_with(
        schedule_id=schedule_id,
        user_id=user_id,
        payload={},
        scheduled_at=scheduled_at,
    )


@pytest.mark.asyncio
async def test_normal_time_schedule_passes_owner_to_scheduler() -> None:
    schedule = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        schedule_type=ScheduleType.TIME,
        time_config=SimpleNamespace(
            cron="*/10 * * * *",
            scheduled_at=None,
        ),
        config={"payload": {"kind": "normal"}},
    )
    client = SchedulerAPIClient("http://scheduler.test")
    client.schedule_cron_job = AsyncMock()

    await client.schedule_job(schedule)

    client.schedule_cron_job.assert_awaited_once_with(
        schedule_id=schedule.id,
        user_id=schedule.user_id,
        cron_expression="*/10 * * * *",
        payload={"kind": "normal", "schedule_id": str(schedule.id)},
        replace_existing=True,
    )


@pytest.mark.asyncio
async def test_workflow_timer_passes_owner_and_exact_wait_ref() -> None:
    adapter = ScheduleControlAdapter.__new__(ScheduleControlAdapter)
    adapter.scheduler = AsyncMock()
    run_id = uuid4()
    user_id = uuid4()
    scheduled_at = "2099-01-01T00:00:00+00:00"

    timer_id = await adapter.schedule_workflow_wake(
        run_id=run_id,
        scheduled_at=scheduled_at,
        pod_id=uuid4(),
        user_id=user_id,
    )

    adapter.scheduler.schedule_once_job.assert_awaited_once_with(
        schedule_id=timer_id,
        user_id=user_id,
        run_date=datetime.fromisoformat(scheduled_at),
        payload={
            "workflow_run_id": str(run_id),
            "wait_ref": str(timer_id),
            "scheduled_at": scheduled_at,
            "source": "workflow_wait_until",
        },
        replace_existing=True,
    )


@pytest.mark.asyncio
async def test_reconcile_time_jobs_replaces_active_and_removes_stale(monkeypatch) -> None:
    cron = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        config={"cron": "*/5 * * * *", "payload": {"kind": "cron"}},
    )
    future = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        config={"scheduled_at": "2099-01-01T00:00:00+00:00"},
    )
    past = SimpleNamespace(
        id=uuid4(),
        user_id=uuid4(),
        config={"scheduled_at": "2000-01-01T00:00:00+00:00"},
    )
    stale_id = uuid4()
    internal_id = uuid4()
    scheduler = Mock()
    scheduler.get_jobs.return_value = [
        SimpleNamespace(
            id=str(stale_id),
            kwargs={"payload": {"schedule_id": str(stale_id)}},
        ),
        SimpleNamespace(
            id=str(past.id),
            kwargs={"payload": {"schedule_id": str(past.id)}},
        ),
        SimpleNamespace(
            id=str(internal_id),
            kwargs={"payload": {"workflow_run_id": str(uuid4())}},
        ),
    ]
    service = scheduler_service.SchedulerService.__new__(
        scheduler_service.SchedulerService
    )
    service.scheduler = scheduler
    service.add_cron_job = Mock()
    service.add_once_job = Mock()
    monkeypatch.setattr(
        scheduler_service,
        "load_active_time_schedules",
        AsyncMock(return_value=[cron, future, past]),
    )

    await service.reconcile_time_schedule_jobs()

    assert scheduler.remove_job.call_args_list == [
        call(str(stale_id)),
        call(str(past.id)),
    ]
    service.add_cron_job.assert_called_once_with(
        schedule_id=cron.id,
        user_id=cron.user_id,
        cron_expression="*/5 * * * *",
        payload={"kind": "cron", "schedule_id": str(cron.id)},
    )
    assert service.add_once_job.call_args.kwargs["user_id"] == future.user_id
    assert service.add_once_job.call_args.kwargs["schedule_id"] == future.id


@pytest.mark.asyncio
async def test_scheduler_starts_paused_and_resumes_after_reconciliation(
    monkeypatch,
) -> None:
    scheduler = Mock(running=True)
    emitter = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    service = scheduler_service.SchedulerService.__new__(
        scheduler_service.SchedulerService
    )
    service.scheduler = scheduler
    service._started = False
    service.reconcile_time_schedule_jobs = AsyncMock()
    monkeypatch.setattr(scheduler_service, "get_event_emitter", lambda: emitter)

    await service.start()

    scheduler.start.assert_called_once_with(paused=True)
    service.reconcile_time_schedule_jobs.assert_awaited_once()
    scheduler.resume.assert_called_once()
    assert service._started is True


@pytest.mark.asyncio
async def test_scheduler_does_not_resume_when_reconciliation_fails(monkeypatch) -> None:
    scheduler = Mock(running=True)
    emitter = SimpleNamespace(start=AsyncMock(), stop=AsyncMock())
    service = scheduler_service.SchedulerService.__new__(
        scheduler_service.SchedulerService
    )
    service.scheduler = scheduler
    service._started = False
    service.reconcile_time_schedule_jobs = AsyncMock(
        side_effect=RuntimeError("reconcile failed")
    )
    monkeypatch.setattr(scheduler_service, "get_event_emitter", lambda: emitter)

    with pytest.raises(RuntimeError, match="reconcile failed"):
        await service.start()

    scheduler.resume.assert_not_called()
    scheduler.shutdown.assert_called_once_with(wait=False)
    emitter.stop.assert_awaited_once()
