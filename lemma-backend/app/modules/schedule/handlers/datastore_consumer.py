from __future__ import annotations

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.events.message_bus import get_message_bus
from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.modules.datastore.domain.events import (
    DATASTORE_EVENTS_STREAM,
    DatastoreRecordEvent,
)
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.datastore_event_handler import DatastoreEventHandler
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.log.log import get_logger
from app.composition.schedule_filter import create_schedule_processor

router = RedisRouter()
logger = get_logger(__name__)


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


@reliable_redis_stream_subscriber(
    router,
    DATASTORE_EVENTS_STREAM,
    group="schedule-datastore-events",
    consumer="schedule-datastore-events-consumer",
)
async def handle_datastore_event(
    event: dict,
    fs_logger: Logger,
    uow_factory: UnitOfWorkFactory = Depends(provide_uow_factory),
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
):
    """Handle datastore record events and fire matching schedules.

    The unified datastore stream also carries datastore/table/file events; only
    record events (``datastore.record.*``) drive schedules, so anything else is
    ignored here.
    """
    event_type = event.get("event_type", "")
    if not event_type.startswith("datastore.record."):
        return

    async def dispatch_schedules() -> None:
        record_event = DatastoreRecordEvent.model_validate(event)
        fs_logger.info(
            "Received DatastoreRecordEvent: %s on %s",
            record_event.operation.value,
            record_event.table_name,
        )

        async with uow_factory() as uow:
            handler = DatastoreEventHandler(
                schedule_repository=ScheduleRepository(
                    uow=uow, message_bus=get_message_bus()
                ),
                schedule_processor=create_schedule_processor(),
            )
            schedule_ids = await handler.handle_datastore_event(record_event)
        if schedule_ids:
            fs_logger.info(
                "Fired %s DATASTORE schedules",
                len(schedule_ids),
            )

    await inbox.process("schedule-datastore-events", event, dispatch_schedules)
