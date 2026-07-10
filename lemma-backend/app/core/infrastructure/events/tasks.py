"""Worker tasks owned by the durable event transport."""

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.events.retention import prune_event_delivery_records
from app.core.infrastructure.jobs.streaq_runtime import streaq_cron
from app.core.log.log import get_logger

logger = get_logger(__name__)


@streaq_cron("0 * * * *", name="prune_event_delivery_records")
async def prune_event_delivery_records_task() -> None:
    deleted = await prune_event_delivery_records(async_session_maker)
    if total := sum(deleted.values()):
        logger.info(
            "Pruned durable event delivery records",
            deleted_count=total,
            categories=deleted,
        )
