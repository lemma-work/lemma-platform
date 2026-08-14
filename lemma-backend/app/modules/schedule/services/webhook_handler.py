from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, Optional
from uuid import UUID

from app.core.authorization.scope import uow_scope
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
from app.modules.schedule.services.webhook_event_mapper import WebhookEventMapper
from app.modules.schedule.services.webhook_schedule_matcher import (
    WebhookScheduleMatcher,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


class WebhookHandler:
    """Service for handling webhooks and matching them to schedules."""

    def __init__(
        self,
        matcher_factory: Callable[[Any], WebhookScheduleMatcher] | None = None,
        uow_factory: Any = None,
        event_mapper: WebhookEventMapper | None = None,
        event_publisher: ScheduleEventPublisher | None = None,
        filter_task_queue: ScheduleFilterTaskQueue | None = None,
    ):
        """Take a way to *open* a unit of work, not an open one.

        This used to be handed a live ``uow`` by the route's dependency, so the
        connection stayed checked out for the whole handler -- through the
        Redis enqueue and the outbox write that follow the match. On a webhook
        route the rate is chosen by whoever is sending, so that is the worst
        place in the app to pin a connection from a fixed-size pool.

        Now the match runs in its own short scope and the fan-out runs with
        nothing held.
        """
        self.matcher_factory = matcher_factory
        self.uow_factory = uow_factory
        if self.matcher_factory is None or self.uow_factory is None:
            raise ValueError("matcher_factory and uow_factory are required")
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

        normalized_payload = self.event_mapper.normalize_payload(
            source=source,
            payload=payload,
        )
        metadata = self.event_mapper.extract_metadata(
            source, normalized_payload, headers
        )
        source_event_id = metadata.get("source_event_id")
        if not isinstance(source_event_id, str) or not source_event_id:
            logger.warning(
                "schedule.webhook_handler.quarantined_webhook_without_stable_provider.degraded"
            )
            raise ScheduleSourceEventIdRequiredError()
        # Phase one: the only database work there is. One indexed lookup, in a
        # scope that ends before anything slow starts.
        async with uow_scope(self.uow_factory) as uow:
            schedules = await self.matcher_factory(uow).match(source, metadata)

        if not schedules:
            return []

        publish_payload = self.event_mapper.event_payload_for_source(
            source, normalized_payload
        )
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
            logger.debug(
                "schedule.webhook_handler.s_has_filter_instruction_offloading.observed"
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
            # A webhook fire has no row owner; the schedule owner runs it.
            user_id=schedule.user_id,
            metadata=metadata,
            source_event_id=source_event_id,
        )
