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
from app.core.log.log import get_logger
from app.composition.schedule_filter import create_schedule_processor

router = RedisRouter()
logger = get_logger(__name__)


@streaq_task(name="handle_llm_filter_task")
async def handle_llm_filter_task(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    schedule_id: str | None = None,
    source_event_id: str | None = None,
) -> None:
    """Apply LLM filtering to a webhook event.

    Loads the schedule in a short-lived DB session, then runs the LLM filter
    and publishes the result with no DB session held — the LLM call can take
    tens of seconds and must not hold a pooled connection idle.
    """
    if schedule_id is None:
        raise ValueError("schedule_id is required")
    if source_event_id is None:
        raise ValueError("source_event_id is required")

    uow_factory = SessionUnitOfWorkFactory(async_session_maker)

    async with uow_factory() as uow:
        schedule = await ScheduleRepository(uow=uow).get(UUID(schedule_id))

    if schedule is None:
        logger.debug(
            "schedule.filter.not_found",
            schedule_id=schedule_id,
        )
        return

    if not schedule.filter_instruction:
        logger.debug(
            'schedule.schedule_consumer.s_has_no_filter_instruction.diagnostic',
            schedule_id=schedule_id,
        )
        return

    processor = create_schedule_processor()
    await processor.process_event(
        schedule=schedule,
        payload=payload,
        metadata=metadata,
        source_event_id=source_event_id,
    )
