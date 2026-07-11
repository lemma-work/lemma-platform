from __future__ import annotations

from typing import Any, Dict, Optional
from uuid import UUID

from app.modules.schedule.domain.interfaces import (
    ScheduleEventPublisher,
    ScheduleFilterTaskQueue,
)
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.infrastructure.adapters.filter_task_queue import (
    StreaqScheduleFilterTaskQueue,
)
from app.modules.schedule.infrastructure.adapters.schedule_event_publisher import (
    DurableScheduleEventPublisher,
)
from app.modules.schedule.domain.errors import ScheduleSourceEventIdRequiredError
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.webhook_event_mapper import WebhookEventMapper
from app.modules.schedule.services.webhook_schedule_matcher import WebhookScheduleMatcher
from app.core.log.log import get_logger

logger = get_logger(__name__)


class WebhookHandler:
    """Service for handling webhooks and matching them to schedules."""

    def __init__(
        self,
        schedule_repository: ScheduleRepository | None = None,
        schedule_matcher: WebhookScheduleMatcher | None = None,
        event_mapper: WebhookEventMapper | None = None,
        event_publisher: ScheduleEventPublisher | None = None,
        filter_task_queue: ScheduleFilterTaskQueue | None = None,
    ):
        self.schedule_repository = schedule_repository
        self.schedule_matcher = schedule_matcher
        if self.schedule_repository is None or self.schedule_matcher is None:
            raise ValueError("schedule_repository and schedule_matcher are required")
        self.event_mapper = event_mapper or WebhookEventMapper()
        self.event_publisher = event_publisher or DurableScheduleEventPublisher()
        self.filter_task_queue = filter_task_queue or StreaqScheduleFilterTaskQueue()

    async def handle_webhook(
        self,
        source: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
    ) -> list[UUID]:
        """Handle incoming webhook and find matching schedules."""
        logger.info("Handling webhook from source: %s", source)

        normalized_payload = self.event_mapper.normalize_payload(
            source=source,
            payload=payload,
        )
        metadata = self.event_mapper.extract_metadata(source, normalized_payload, headers)
        source_event_id = metadata.get("source_event_id")
        if not isinstance(source_event_id, str) or not source_event_id:
            logger.warning("Quarantined webhook without a stable provider event id")
            raise ScheduleSourceEventIdRequiredError()
        schedules = await self.schedule_matcher.match(source, metadata)

        if not schedules:
            logger.info("No matching schedules found for %s webhook", source)
            return []

        publish_payload = self.event_mapper.event_payload_for_source(source, normalized_payload)
        schedule_ids: list[UUID] = []
        for schedule in schedules:
            await self._process_matched_schedule(
                schedule=schedule,
                payload=publish_payload,
                metadata=metadata,
                source_event_id=source_event_id,
            )
            schedule_ids.append(schedule.id)

        return schedule_ids

    async def _process_matched_schedule(
        self,
        schedule: ScheduleEntity,
        payload: Dict[str, Any],
        metadata: Dict[str, Any],
        source_event_id: str,
    ) -> None:
        """Publish schedule or defer through LLM filter queue when needed."""
        if schedule.filter_instruction:
            logger.info(
                "Schedule %s has filter instruction, offloading to background task",
                schedule.id,
            )
            await self.filter_task_queue.enqueue(
                schedule_id=schedule.id,
                payload=payload,
                metadata=metadata,
                source_event_id=source_event_id,
            )
            return

        await self.event_publisher.publish_schedule_fired(
            schedule=schedule,
            payload=payload,
            metadata=metadata,
            source_event_id=source_event_id,
        )
