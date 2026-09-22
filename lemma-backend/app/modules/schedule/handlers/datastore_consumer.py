from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

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
from app.modules.schedule.repositories.datastore_watch import (
    pod_has_active_datastore_schedules,
)
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.datastore_event_handler import DatastoreEventHandler
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.log.log import get_logger
from app.modules.schedule.infrastructure.adapters.system_model_filter import (
    create_schedule_processor,
)

router = RedisRouter()
logger = get_logger(__name__)


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


#: "Is anything in this pod watching its tables?", as a seam rather than a
#: constructor call. Injected for the same reason ``inbox`` is: it is the
#: guard that decides whether the expensive path runs at all, so a test has to
#: be able to state its answer without standing in front of the repository it
#: happens to be built from.
PodWatchLookup = Callable[[UUID], Awaitable[bool]]


def provide_pod_watch_lookup() -> PodWatchLookup:
    async def pod_is_watched(pod_id: UUID) -> bool:
        async with provide_uow_factory()() as uow:
            return await pod_has_active_datastore_schedules(uow.session, pod_id)

    return pod_is_watched


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
    pod_is_watched: PodWatchLookup = Depends(provide_pod_watch_lookup),
):
    """Handle datastore record events and fire matching schedules.

    The unified datastore stream also carries datastore/table/file events; only
    record events (``datastore.record.*``) drive schedules, so anything else is
    ignored here.

    Two filters, cheapest first, and the order is the whole point. This is by a
    wide margin the noisiest stream on the platform -- every row written
    anywhere lands here -- while only 42 of 1282 live pods hold an active
    DATASTORE schedule. Claiming the inbox before knowing whether anyone is
    watching meant ~97% of record events each bought a durable Postgres row, a
    claim transaction, a jsonb match query, an unmatched-lookup query and a
    completion transaction, to decide there was nothing to do.

    That is what stalled production: one tenant's bulk import (100% of a
    300-entry sample) put this consumer minutes behind, its pending list grew
    past 28,000, and a pending list is what stops the stream being trimmed --
    so the stream kept growing and entries aged out undelivered. Other
    tenants' triggers simply never fired.
    """
    event_type = event.get("event_type", "")
    if not event_type.startswith("datastore.record."):
        return

    pod_id = event.get("pod_id")
    if pod_id is None:
        return

    # Before the inbox, deliberately. The inbox makes *side effects*
    # exactly-once; a pod nobody has pointed a DATASTORE schedule at has no
    # side effect to protect, so recording the delivery buys nothing and costs
    # a row that outlives the event. Doing nothing twice is still doing
    # nothing, which is what makes skipping it safe rather than merely cheap.
    if not await pod_is_watched(UUID(str(pod_id))):
        return

    async def dispatch_schedules() -> None:
        record_event = DatastoreRecordEvent.model_validate(event)

        async with uow_factory() as uow:
            handler = DatastoreEventHandler(
                schedule_repository=ScheduleRepository(
                    uow=uow, message_bus=get_message_bus()
                ),
                schedule_processor=create_schedule_processor(),
            )
            schedule_ids = await handler.handle_datastore_event(record_event)
        if schedule_ids:
            logger.debug(
                "schedule.datastore_consumer.fired_s_datastore_schedules.observed",
                count=len(schedule_ids),
            )

    await inbox.process("schedule-datastore-events", event, dispatch_schedules)
