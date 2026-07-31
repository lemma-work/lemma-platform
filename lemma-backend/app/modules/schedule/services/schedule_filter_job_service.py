"""Application service for deferred schedule LLM-filter jobs."""

from __future__ import annotations

from uuid import UUID

from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from app.core.log.log import get_logger

logger = get_logger(__name__)


class ScheduleFilterJobService:
    """Processes deferred schedule filter jobs using repositories and services."""

    def __init__(
        self,
        schedule_repository: ScheduleRepository,
        processor: ScheduleProcessor,
    ):
        self._schedule_repository = schedule_repository
        self._processor = processor

    async def process(
        self,
        *,
        schedule_id: str | None = None,
        payload: dict,
        metadata: dict,
        source_event_id: str,
    ) -> None:
        if schedule_id is None:
            raise ValueError("schedule_id is required")
        schedule = await self._schedule_repository.get(UUID(schedule_id))
        if schedule is None:
            logger.debug(
                "schedule.filter.not_found",
                schedule_id=schedule_id,
            )
            return

        if not schedule.filter_instruction:
            logger.debug(
                'schedule.schedule_filter_job_service.s_has_no_filter_instruction.diagnostic',
                schedule_id=schedule_id,
            )
            return

        await self._processor.process_event(
            schedule=schedule,
            payload=payload,
            metadata=metadata,
            source_event_id=source_event_id,
        )
