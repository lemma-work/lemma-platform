import pytest
from unittest.mock import AsyncMock
from uuid import uuid4

from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.services.trigger_fire import FireOutcome, TriggerEvent
from app.modules.schedule.services.webhook_handler import WebhookHandler
from app.modules.schedule.services.webhook_schedule_matcher import (
    WebhookScheduleMatcher,
)
from app.modules.schedule.domain.errors import ScheduleSourceEventIdRequiredError


class _Fired:
    """Records each fire the handler asks for, in place of ``fire_trigger``."""

    def __init__(self) -> None:
        self.calls: list[tuple[ScheduleEntity, TriggerEvent]] = []

    async def __call__(self, uow, schedule, event) -> FireOutcome:
        del uow
        self.calls.append((schedule, event))
        return FireOutcome.STARTED


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
    fired = _Fired()

    matcher = WebhookScheduleMatcher(
        schedule_repository=schedule_repo,
    )
    handler = WebhookHandler(
        matcher_factory=lambda _uow: matcher,
        uow_factory=_null_uow_factory,
        fire=fired,
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
    assert len(fired.calls) == 1


@pytest.mark.asyncio
async def test_handle_webhook_composio_missing_provider_id():
    schedule_repo = AsyncMock()

    matcher = WebhookScheduleMatcher(
        schedule_repository=schedule_repo,
    )
    handler = WebhookHandler(
        matcher_factory=lambda _uow: matcher,
        uow_factory=_null_uow_factory,
        fire=_Fired(),
    )

    payload = {"type": "some_event", "data": {}}
    with pytest.raises(ScheduleSourceEventIdRequiredError):
        await handler.handle_webhook(source="composio", payload=payload)

    schedule_repo.find_by_config.assert_not_called()


@pytest.mark.asyncio
async def test_handle_webhook_composio_v3_success():
    schedule_repo = AsyncMock()
    fired = _Fired()

    matcher = WebhookScheduleMatcher(
        schedule_repository=schedule_repo,
    )
    handler = WebhookHandler(
        matcher_factory=lambda _uow: matcher,
        uow_factory=_null_uow_factory,
        fire=fired,
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
    [(_, event)] = fired.calls
    assert event.payload == payload["data"]
    assert event.metadata["event_type"] == payload["metadata"]["trigger_slug"]
    assert event.metadata["webhook_event_type"] == "composio.trigger.message"
    assert event.source_event_id == "evt_123"
