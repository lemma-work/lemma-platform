"""Background jobs for schedule processing.

Note: workflow module owns consumption of ``schedule_events`` stream for starting/resuming
workflow runs. Keeping an additional no-op subscriber here can cause nondeterministic
message consumption.
"""

from typing import Any
from uuid import UUID
from faststream.redis import RedisRouter

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_runtime import streaq_task
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from app.core.log.log import get_logger

router = RedisRouter()
logger = get_logger(__name__)


@streaq_task(name="handle_llm_filter_task")
async def handle_llm_filter_task(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    schedule_id: str | None = None,
) -> None:
    """Apply LLM filtering to a webhook event.

    Loads the schedule in a short-lived DB session, then runs the LLM filter
    and publishes the result with no DB session held — the LLM call can take
    tens of seconds and must not hold a pooled connection idle.
    """
    if schedule_id is None:
        raise ValueError("schedule_id is required")
    logger.info(f"Processing LLM filtering for schedule {schedule_id}")

    uow_factory = SessionUnitOfWorkFactory(async_session_maker)

    async with uow_factory() as uow:
        schedule = await ScheduleRepository(uow=uow).get(UUID(schedule_id))

    if schedule is None:
        logger.error("Schedule %s not found for LLM filtering", schedule_id)
        return

    if not schedule.filter_instruction:
        logger.warning("Schedule %s has no filter instruction, skipping", schedule_id)
        return

    processor = ScheduleProcessor()
    fired = await processor.process_event(
        schedule=schedule,
        payload=payload,
        metadata=metadata,
    )
    if not fired:
        logger.info("Schedule %s filtered out by LLM, skipping event", schedule_id)
