"""The filter task: who a deferred fire runs as, what it records, and retry.

Webhook and datastore schedules with a `filter_instruction` both end here, so
these are the properties the datastore path gave up evaluating inline for.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

import pytest
from pydantic_ai.exceptions import ModelHTTPError
from streaq import StreaqRetry

from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleFireStatus,
    ScheduleType,
)
from app.modules.schedule.handlers.schedule_consumer import (
    FILTER_RETRY_DELAY,
    DeferredFilter,
    run_deferred_filter,
)
from app.modules.schedule.infrastructure.adapters.filter_task_queue import (
    StreaqScheduleFilterTaskQueue,
)
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from app.modules.schedule.tests.fakes import (
    InMemoryFilterTaskStore,
    RecordedFire,
    RecordingScheduleEventPublisher,
    ScriptedScheduleFilter,
)
from app.modules.usage.contracts import ProviderAttemptsExhaustedError


def _store_with_filtered_schedule() -> tuple[InMemoryFilterTaskStore, ScheduleEntity]:
    schedule = ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=uuid4(),
        schedule_type=ScheduleType.DATASTORE,
        config={"table_name": "tickets", "operations": ["INSERT"]},
        filter_instruction="Only tickets that need a human.",
    )
    return InMemoryFilterTaskStore(schedules={schedule.id: schedule}), schedule


def _job(schedule: ScheduleEntity, **overrides: Any) -> DeferredFilter:
    fields: dict[str, Any] = {
        "schedule_id": schedule.id,
        "payload": {"id": "rec_1"},
        "metadata": {
            "table_name": "tickets",
            "event_occurred_at": datetime.now(timezone.utc).isoformat(),
        },
        "source_event_id": str(uuid4()),
    }
    fields.update(overrides)
    return DeferredFilter(**fields)


@pytest.mark.asyncio
async def test_a_datastore_fire_runs_as_the_row_owner():
    """Filtering it elsewhere must not change who the run belongs to."""
    store, schedule = _store_with_filtered_schedule()
    publisher = RecordingScheduleEventPublisher()
    row_owner = uuid4()
    job = _job(schedule, user_id=row_owner)

    await run_deferred_filter(
        job,
        store=store,
        processor=ScheduleProcessor(ScriptedScheduleFilter(), publisher),
    )

    [fire] = publisher.fired
    assert fire.user_id == row_owner
    assert fire.source_event_id == job.source_event_id


@pytest.mark.asyncio
async def test_a_fire_without_an_owner_runs_as_the_schedule_owner():
    store, schedule = _store_with_filtered_schedule()
    publisher = RecordingScheduleEventPublisher()

    await run_deferred_filter(
        _job(schedule),
        store=store,
        processor=ScheduleProcessor(ScriptedScheduleFilter(), publisher),
    )

    assert [fire.user_id for fire in publisher.fired] == [schedule.user_id]


@pytest.mark.asyncio
async def test_a_declined_event_is_recorded_as_filtered():
    """The handler no longer stamps a filtered schedule, so the task must."""
    store, schedule = _store_with_filtered_schedule()
    publisher = RecordingScheduleEventPublisher()

    await run_deferred_filter(
        _job(schedule),
        store=store,
        processor=ScheduleProcessor(ScriptedScheduleFilter(proceed=False), publisher),
    )

    assert publisher.fired == []
    assert store.fires == [RecordedFire(schedule.id, ScheduleFireStatus.FILTERED, None)]


@pytest.mark.asyncio
async def test_a_provider_outage_is_retried_not_dropped():
    """streaq retries only `StreaqRetry`; anything else would end the job."""
    store, schedule = _store_with_filtered_schedule()
    publisher = RecordingScheduleEventPublisher()
    model = ScriptedScheduleFilter(failure=ProviderAttemptsExhaustedError())

    with pytest.raises(StreaqRetry) as raised:
        await run_deferred_filter(
            _job(schedule),
            store=store,
            processor=ScheduleProcessor(model, publisher),
        )

    assert raised.value.delay == FILTER_RETRY_DELAY
    assert publisher.fired == []
    # The type and nothing of the provider's wording: `last_error` is shown to
    # the schedule's owner.
    assert store.fires == [
        RecordedFire(
            schedule.id,
            ScheduleFireStatus.ERROR,
            "ProviderAttemptsExhaustedError: schedule filter failed",
        )
    ]


@pytest.mark.asyncio
async def test_a_rejection_the_model_layer_would_not_retry_is_not_retried():
    """A 400 will be a 400 again; retrying it only spends the attempts."""
    store, schedule = _store_with_filtered_schedule()
    rejected = ModelHTTPError(status_code=400, model_name="system", body=None)
    model = ScriptedScheduleFilter(failure=rejected)

    with pytest.raises(ModelHTTPError):
        await run_deferred_filter(
            _job(schedule),
            store=store,
            processor=ScheduleProcessor(model, RecordingScheduleEventPublisher()),
        )


@pytest.mark.asyncio
async def test_a_datastore_fire_reports_its_latency_once_the_filter_passes(caplog):
    store, schedule = _store_with_filtered_schedule()

    with caplog.at_level("INFO"):
        await run_deferred_filter(
            _job(schedule),
            store=store,
            processor=ScheduleProcessor(
                ScriptedScheduleFilter(), RecordingScheduleEventPublisher()
            ),
        )

    assert "schedule.fire.latency_ms" in caplog.text


class _RecordingJobQueue:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, dict[str, Any]]] = []

    async def enqueue(self, job_name: str, **kwargs: Any) -> None:
        self.jobs.append((job_name, kwargs))

    async def abort(self, job_id: str, *, timeout_seconds: float | None = None) -> bool:
        return False


@pytest.mark.asyncio
async def test_a_webhook_filter_job_carries_no_owner_argument():
    """Same bytes as before, so a worker from before the argument still runs it."""
    jobs = _RecordingJobQueue()
    schedule_id, source_event_id = uuid4(), "evt_1"

    await StreaqScheduleFilterTaskQueue(jobs).enqueue(
        schedule_id=schedule_id,
        payload={},
        metadata={},
        source_event_id=source_event_id,
    )

    [(name, kwargs)] = jobs.jobs
    assert name == "handle_llm_filter_task"
    assert "user_id" not in kwargs
    assert kwargs["_job_id"] == f"schedule-filter:{schedule_id}:{source_event_id}"


@pytest.mark.asyncio
async def test_a_datastore_filter_job_carries_the_row_owner():
    jobs = _RecordingJobQueue()
    row_owner = uuid4()

    await StreaqScheduleFilterTaskQueue(jobs).enqueue(
        schedule_id=uuid4(),
        payload={},
        metadata={},
        source_event_id="evt_1",
        user_id=row_owner,
    )

    [(_, kwargs)] = jobs.jobs
    assert kwargs["user_id"] == str(row_owner)
