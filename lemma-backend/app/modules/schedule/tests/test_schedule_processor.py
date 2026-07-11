from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from app.modules.usage.domain.errors import UsageLimitExceededError


def _schedule(**updates) -> ScheduleEntity:
    values = {
        "id": uuid4(),
        "user_id": uuid4(),
        "pod_id": uuid4(),
        "schedule_type": ScheduleType.WEBHOOK,
        "config": {"source": "custom"},
        "filter_instruction": "Accept relevant events",
    }
    values.update(updates)
    return ScheduleEntity(**values)


@pytest.mark.asyncio
async def test_processor_rejects_missing_or_inactive_schedule():
    processor = ScheduleProcessor(
        filter_service=AsyncMock(), event_publisher=AsyncMock()
    )

    with pytest.raises(ValueError, match="schedule is required"):
        await processor.process_event(schedule=None, payload={})
    assert await processor.process_event(
        schedule=_schedule(is_active=False), payload={}
    ) is False
    processor.event_publisher.publish_schedule_fired.assert_not_awaited()


@pytest.mark.asyncio
async def test_processor_records_filtered_decision_without_publishing():
    filter_service = AsyncMock()
    filter_service.filter_event.return_value = (False, {"reason": "not relevant"})
    publisher = AsyncMock()
    processor = ScheduleProcessor(filter_service, publisher)

    result = await processor.process_event(schedule=_schedule(), payload={"id": 1})

    assert result is False
    publisher.publish_schedule_fired.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure",
    [UsageLimitExceededError(), RuntimeError("provider unavailable")],
)
async def test_processor_rethrows_filter_failures_for_durable_retry(failure):
    filter_service = AsyncMock()
    filter_service.filter_event.side_effect = failure
    processor = ScheduleProcessor(filter_service, AsyncMock())

    with pytest.raises(type(failure)):
        await processor.process_event(schedule=_schedule(), payload={"id": 1})


@pytest.mark.asyncio
async def test_processor_publishes_filter_output_and_source_identity():
    filter_service = AsyncMock()
    filter_service.filter_event.return_value = (True, {"category": "urgent"})
    publisher = AsyncMock()
    processor = ScheduleProcessor(filter_service, publisher)
    schedule = _schedule()

    assert await processor.process_event(
        schedule=schedule,
        payload={"id": 1},
        metadata={"provider": "custom"},
        source_event_id="provider:event-1",
    ) is True
    publisher.publish_schedule_fired.assert_awaited_once_with(
        schedule=schedule,
        payload={"id": 1},
        metadata={"provider": "custom"},
        llm_output={"category": "urgent"},
        source_event_id="provider:event-1",
    )
