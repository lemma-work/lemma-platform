import pytest
from unittest.mock import AsyncMock
from uuid import uuid4

from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.services.webhook_handler import WebhookHandler
from app.modules.schedule.services.webhook_schedule_matcher import WebhookScheduleMatcher
from app.modules.schedule.domain.errors import ScheduleSourceEventIdRequiredError



def _null_uow_factory():
    """A unit of work that opens nothing.

    The handler now opens its own short scope for the schedule lookup instead
    of being handed a live one, which is the point of the change -- the Redis
    enqueue and outbox write that follow must not run with a connection
    checked out. These tests inject a mock matcher, so the scope has nothing to
    do; it only has to be enterable.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def _scope():
        yield None

    return _scope()


@pytest.mark.asyncio
async def test_handle_webhook_composio_success(composio_gmail_event):
    schedule_repo = AsyncMock()
    event_publisher = AsyncMock()

    matcher = WebhookScheduleMatcher(
        schedule_repository=schedule_repo,
    )
    handler = WebhookHandler(
        matcher_factory=lambda _uow: matcher,
        uow_factory=_null_uow_factory,
        event_publisher=event_publisher,
    )

    schedule_id = uuid4()
    provider_id = composio_gmail_event["data"]["trigger_nano_id"]

    schedule_entity = ScheduleEntity(
        id=schedule_id,
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        connector_trigger_id=composio_gmail_event["type"],
        account_id=uuid4(),
        config={"provider_trigger_id": provider_id},
        is_active=True,
    )
    schedule_repo.find_by_config.return_value = [schedule_entity]

    composio_gmail_event["id"] = "evt_fixture_123"
    result_ids = await handler.handle_webhook(
        source="composio", payload=composio_gmail_event
    )

    assert result_ids == [schedule_id]
    schedule_repo.find_by_config.assert_called_once_with(
        schedule_type=ScheduleType.WEBHOOK,
        criteria={"provider_trigger_id": provider_id},
    )
    event_publisher.publish_schedule_fired.assert_called_once()


@pytest.mark.asyncio
async def test_handle_webhook_composio_missing_provider_id():
    schedule_repo = AsyncMock()

    matcher = WebhookScheduleMatcher(
        schedule_repository=schedule_repo,
    )
    handler = WebhookHandler(
        matcher_factory=lambda _uow: matcher,
        uow_factory=_null_uow_factory,
        event_publisher=AsyncMock(),
    )

    payload = {"type": "some_event", "data": {}}
    with pytest.raises(ScheduleSourceEventIdRequiredError):
        await handler.handle_webhook(source="composio", payload=payload)

    schedule_repo.find_by_config.assert_not_called()


@pytest.mark.asyncio
async def test_handle_webhook_composio_v3_success():
    schedule_repo = AsyncMock()
    event_publisher = AsyncMock()

    matcher = WebhookScheduleMatcher(
        schedule_repository=schedule_repo,
    )
    handler = WebhookHandler(
        matcher_factory=lambda _uow: matcher,
        uow_factory=_null_uow_factory,
        event_publisher=event_publisher,
    )

    schedule_id = uuid4()
    provider_id = "ti_v3_123"
    payload = {
        "type": "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER",
        "webhook_type": "composio.trigger.message",
        "metadata": {
            "trigger_slug": "GOOGLECALENDAR_GOOGLE_CALENDAR_EVENT_SYNC_TRIGGER",
            "trigger_id": provider_id,
            "connected_account_id": "ca_123",
        },
        "data": {
            "event_id": "evt_123",
            "summary": "Workflow Discussion",
        },
    }

    schedule_entity = ScheduleEntity(
        id=schedule_id,
        user_id=uuid4(),
        schedule_type=ScheduleType.WEBHOOK,
        connector_trigger_id=payload["type"],
        account_id=uuid4(),
        config={"provider_trigger_id": provider_id},
        is_active=True,
    )
    schedule_repo.find_by_config.return_value = [schedule_entity]

    result_ids = await handler.handle_webhook(source="composio", payload=payload)

    assert result_ids == [schedule_id]
    schedule_repo.find_by_config.assert_called_once_with(
        schedule_type=ScheduleType.WEBHOOK,
        criteria={"provider_trigger_id": provider_id},
    )
    publish_call = event_publisher.publish_schedule_fired.call_args.kwargs
    assert publish_call["payload"] == payload["data"]
    assert publish_call["metadata"]["event_type"] == payload["metadata"]["trigger_slug"]
    assert (
        publish_call["metadata"]["webhook_event_type"]
        == "composio.trigger.message"
    )
    assert publish_call["source_event_id"] == "evt_123"
