"""Worker tasks owned by the durable event transport."""

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.events.retention import prune_event_delivery_records
from app.core.infrastructure.events.stream_budget import trim_streams_to_budget
from app.core.infrastructure.jobs.streaq_runtime import streaq_cron
from app.core.log.log import get_logger

logger = get_logger(__name__)


@streaq_cron("7 * * * *", name="prune_event_delivery_records")
async def prune_event_delivery_records_task() -> None:
    deleted = await prune_event_delivery_records(async_session_maker)
    if total := sum(deleted.values()):
        logger.debug(
            "infrastructure.tasks.pruned_durable_event_delivery_records.observed",
            deleted_count=total,
        )


@streaq_cron("23 * * * *", name="trim_streams_to_budget")
async def trim_streams_to_budget_task() -> None:
    """Hourly backstop for streams whose payloads outgrew their entry count.

    Off the hour from the other event cron so two Redis-heavy passes do not
    land together.
    """
    from app.core.infrastructure.redis.client import get_redis

    reclaimed = await trim_streams_to_budget(get_redis())
    if total := sum(reclaimed.values()):
        logger.info(
            "redis.stream.budget_trimmed.observed",
            streams=len(reclaimed),
            reclaimed_bytes=total,
        )
