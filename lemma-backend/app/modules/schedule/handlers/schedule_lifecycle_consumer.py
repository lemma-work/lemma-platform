"""Apply runtime side effects for schedule lifecycle events."""

from __future__ import annotations

from faststream import Depends, Logger
from faststream.redis import RedisRouter

from app.core.infrastructure.events.inbox import (
    EventInboxPort,
    provide_domain_event_inbox,
)
from app.core.infrastructure.events.stream_subscriber import (
    reliable_redis_stream_subscriber,
)
from app.modules.schedule.domain.events.schedule import (
    ScheduleDeactivated,
    ScheduleEvents,
)
from app.modules.schedule.domain.schedule import ScheduleType

router = RedisRouter()


@reliable_redis_stream_subscriber(
    router,
    ScheduleEvents.STREAM,
    group="schedule-runtime-lifecycle",
    consumer="schedule-runtime-lifecycle-consumer",
)
async def on_schedule_deactivated(
    event: dict,
    fs_logger: Logger,
    inbox: EventInboxPort = Depends(provide_domain_event_inbox),
) -> None:
    if event.get("event_type") != ScheduleDeactivated.get_event_type():
        return

    async def apply_runtime_state() -> None:
        parsed = ScheduleDeactivated.model_validate(event)
        if parsed.schedule_type != ScheduleType.TIME:
            return
        # Deactivation is the removal. The poller's due query filters on
        # `is_active`, and this event fires because the row was just
        # deactivated -- so by the time it arrives the schedule is already
        # unclaimable. There is no separate job to delete any more.
        fs_logger.debug(
            "schedule.time_job.removed",
            schedule_id=str(parsed.schedule_id),
        )

    await inbox.process("schedule.runtime-lifecycle", event, apply_runtime_state)
