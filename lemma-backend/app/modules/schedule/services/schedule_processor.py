"""Schedule processor service."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Literal, Optional
from uuid import UUID

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


@dataclass(frozen=True)
class ProcessedEvent:
    """What became of one event: fired, skipped by the filter, or ignored.

    `llm_output` is the filter's answers whichever way it went, so a skip can be
    recorded with the reason it was skipped.
    """

    outcome: Literal["fired", "filtered", "inactive"]
    llm_output: dict[str, object] | None = None

    @property
    def fired(self) -> bool:
        return self.outcome == "fired"


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
        user_id: UUID,
        metadata: Optional[Dict[str, Any]] = None,
        source_event_id: str | None = None,
    ) -> ProcessedEvent:
        """Process schedule event and publish when accepted."""
        if schedule is None:
            raise ValueError("schedule is required")
        if not schedule.is_active:
            return ProcessedEvent("inactive")

        llm_output: dict[str, object] | None = None

        if schedule.filter_instruction:
            if self.filter_service is None:
                raise RuntimeError("Schedule filter adapter is not configured")
            # Every filter failure propagates, quota included. Which of them is
            # worth retrying is a policy question, and it is answered at the
            # task boundary in `handle_llm_filter_task` — this layer does not
            # know whether its caller can retry.
            verdict = await self.filter_service.filter_event(
                instruction=schedule.filter_instruction,
                output_schema=schedule.filter_output_schema,
                event_payload=payload,
                schedule=schedule,
            )
            llm_output = verdict.output
            if not verdict.proceed:
                logger.debug("schedule.schedule_processor.s_filtered_out_llm.observed")
                return ProcessedEvent("filtered", llm_output)

        if not source_event_id:
            raise ValueError("source_event_id is required to publish a schedule fire")

        await self.event_publisher.publish_schedule_fired(
            schedule=schedule,
            payload=payload,
            user_id=user_id,
            metadata=metadata,
            llm_output=llm_output,
            source_event_id=source_event_id,
        )
        return ProcessedEvent("fired", llm_output)
