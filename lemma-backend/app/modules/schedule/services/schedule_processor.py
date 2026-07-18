"""Schedule processor service."""

from __future__ import annotations

from typing import Any, Dict, Optional

from app.modules.schedule.domain.interfaces import (
    ScheduleEventFilter,
    ScheduleEventPublisher,
)
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.infrastructure.adapters.schedule_event_publisher import (
    DurableScheduleEventPublisher,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


class ScheduleProcessor:
    """Service to process schedules and emit events."""

    def __init__(
        self,
        filter_service: ScheduleEventFilter | None = None,
        event_publisher: ScheduleEventPublisher | None = None,
    ):
        self.filter_service = filter_service
        self.event_publisher = event_publisher or DurableScheduleEventPublisher()

    async def process_event(
        self,
        *,
        schedule: ScheduleEntity | None = None,
        payload: Dict[str, Any],
        metadata: Optional[Dict[str, Any]] = None,
        source_event_id: str | None = None,
    ) -> bool:
        """Process schedule event and publish when accepted."""
        if schedule is None:
            raise ValueError("schedule is required")
        if not schedule.is_active:
            return False

        llm_output: Optional[Dict[str, Any]] = None

        if schedule.filter_instruction:
            if self.filter_service is None:
                raise RuntimeError("Schedule filter adapter is not configured")
            should_proceed, llm_output = await self.filter_service.filter_event(
                instruction=schedule.filter_instruction,
                output_schema=schedule.filter_output_schema,
                event_payload=payload,
                schedule=schedule,
            )

            if not should_proceed:
                logger.debug("schedule.schedule_processor.s_filtered_out_llm.observed")
                return False

        await self.event_publisher.publish_schedule_fired(
            schedule=schedule,
            payload=payload,
            metadata=metadata,
            llm_output=llm_output,
            source_event_id=source_event_id,
        )
        return True
