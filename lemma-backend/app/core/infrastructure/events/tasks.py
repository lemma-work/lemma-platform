"""Worker tasks owned by the durable event transport."""

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.events.config import event_transport_settings
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
    from app.core.infrastructure.events.group_reaper import (
        reap_abandoned_consumer_groups,
    )
    from app.core.infrastructure.redis.client import get_redis

    client = get_redis()
    # Before the trim, not after: a group nobody consumes any more pins the
    # XTRIM watermark, so reaping it is what lets the same pass reclaim the
    # bytes rather than give up and log that it could not.
    abandoned = await reap_abandoned_consumer_groups(client)
    if abandoned:
        # "detected", not "reaped": destruction is off by default, so the usual
        # reading of this line is "these are candidates", and a line that says
        # they were reaped when nothing was would be read once and trusted
        # afterwards. `destroyed` carries which it was.
        logger.info(
            "redis.stream.abandoned_consumer_groups_detected.observed",
            group_count=len(abandoned),
            destroyed=event_transport_settings.redis_stream_group_destroy_enabled,
        )
    reclaimed = await trim_streams_to_budget(client)
    if total := sum(reclaimed.values()):
        logger.info(
            "redis.stream.budget_trimmed.observed",
            streams=len(reclaimed),
            reclaimed_bytes=total,
        )
