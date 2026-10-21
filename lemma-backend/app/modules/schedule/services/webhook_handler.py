from __future__ import annotations

from collections.abc import Callable
from typing import Any, Dict, Optional
from uuid import UUID

from app.core.authorization.scope import uow_scope
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.contracts.webhook_source import NormalizedWebhook
from app.modules.schedule.domain.errors import ScheduleSourceEventIdRequiredError
from app.modules.schedule.services.trigger_fire import (
    FireTrigger,
    TriggerEvent,
    fire_trigger,
    jsonable,
)
from app.modules.schedule.services.webhook_event_mapper import WebhookEventMapper
from app.modules.schedule.services.webhook_schedule_matcher import (
    WebhookScheduleMatcher,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


def _narrowed(
    schedules: list[ScheduleEntity], normalized: NormalizedWebhook
) -> list[ScheduleEntity]:
    """What the source says about the delivery beyond its routing key."""
    if normalized.refine is not None:
        # The routing key is deliberately coarse -- see `match_criteria`.
        # Schedules that scoped themselves further are filtered here.
        schedules = [s for s in schedules if normalized.refine(s.config or {})]
    if normalized.account_id is not None:
        # Verified with one account's own secret: only that account's.
        schedules = [s for s in schedules if str(s.account_id) == normalized.account_id]
    return schedules


class WebhookHandler:
    """Service for handling webhooks and matching them to schedules."""

    def __init__(
        self,
        matcher_factory: Callable[[Any], WebhookScheduleMatcher] | None = None,
        uow_factory: Any = None,
        event_mapper: WebhookEventMapper | None = None,
        fire: FireTrigger = fire_trigger,
    ):
        """Take a way to *open* a unit of work, not an open one.

        The match and every fire it causes run in one short transaction of
        their own: run rows and the work items that start their targets (or
        ask their filters) commit together, and nothing slow happens inside it.
        """
        self.matcher_factory = matcher_factory
        self.uow_factory = uow_factory
        if self.matcher_factory is None or self.uow_factory is None:
            raise ValueError("matcher_factory and uow_factory are required")
        self.event_mapper = event_mapper or WebhookEventMapper()
        self._fire = fire

    async def handle_webhook(
        self,
        source: str,
        payload: Dict[str, Any],
        headers: Optional[Dict[str, str]] = None,
        normalized: NormalizedWebhook | None = None,
    ) -> list[UUID]:
        """Handle incoming webhook and find matching schedules.

        A source plugin that states its own routing key passes `normalized` and
        the mapper is not consulted. The mapper remains the path for the sources
        that predate the plugins -- Composio's branch in particular, whose exact
        output is pinned by tests, and which was moved onto a plugin on the
        explicit condition that its behaviour did not change with it.
        """

        if normalized is not None and normalized.match is not None:
            metadata: Dict[str, Any] = {**normalized.match, **normalized.context}
            metadata.setdefault("source_event_id", normalized.source_event_id)
            normalized_payload = normalized.payload
        else:
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
        if normalized is not None and normalized.match is not None:
            publish_payload = normalized.payload
        else:
            publish_payload = self.event_mapper.event_payload_for_source(
                source, normalized_payload
            )
        event = TriggerEvent(
            source_event_id=source_event_id,
            payload=jsonable(publish_payload),
            metadata=jsonable(metadata),
        )
        schedule_ids: list[UUID] = []
        async with uow_scope(self.uow_factory) as uow:
            matcher = self.matcher_factory(uow)
            if normalized is not None and normalized.match is not None:
                schedules = await matcher.match_criteria(normalized.match)
            else:
                schedules = await matcher.match(source, metadata)
            if normalized is not None:
                schedules = _narrowed(schedules, normalized)
            for schedule in schedules:
                # A webhook fire has no row owner; the schedule owner runs it.
                await self._fire(uow, schedule, event)
                schedule_ids.append(schedule.id)
        return schedule_ids
