"""Schedule processor service."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Dict, Optional
from uuid import UUID

from app.modules.schedule.domain.interfaces import (
    ScheduleEventFilter,
    ScheduleEventPublisher,
    ScheduleFilterVerdict,
)
from app.modules.schedule.domain.ports import ScheduleEventTriage
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.domain.triage import TriageRoute, TriageVerdict
from app.modules.schedule.infrastructure.adapters.schedule_event_publisher import (
    DurableScheduleEventPublisher,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class ScheduleEventOutcome:
    """What one event came to for one schedule.

    `verdict` is the filter's, when the schedule has one. A verdict that did not
    proceed is a skip the caller records -- a `FILTERED` run carrying the
    decision -- which is how a skipped trigger is told apart from one that
    never arrived. A triage's `ignore` is a skip the same way, carrying the
    triage's decision; its `digest` and `ask` are `held`, for the caller to
    record as a `HELD` run.
    """

    fired: bool
    verdict: ScheduleFilterVerdict | None = None
    triage: TriageVerdict | None = None

    @property
    def filtered(self) -> ScheduleFilterVerdict | None:
        """The verdict that turned the event down, if one did."""
        if self.verdict is None or self.verdict.proceed:
            return None
        return self.verdict

    @property
    def held(self) -> TriageVerdict | None:
        """The triage that held the event for its digest or a person, if one did."""
        if self.triage is None or not self.triage.holds:
            return None
        return self.triage


class ScheduleProcessor:
    """Service to process schedules and emit events."""

    def __init__(
        self,
        filter_service: ScheduleEventFilter | None = None,
        event_publisher: ScheduleEventPublisher | None = None,
        triage_service: ScheduleEventTriage | None = None,
    ):
        self.filter_service = filter_service
        self.event_publisher = event_publisher or DurableScheduleEventPublisher()
        self.triage_service = triage_service

    async def process_event(
        self,
        *,
        schedule: ScheduleEntity | None = None,
        payload: Dict[str, Any],
        user_id: UUID,
        metadata: Optional[Dict[str, Any]] = None,
        source_event_id: str | None = None,
        personal: bool = False,
    ) -> ScheduleEventOutcome:
        """Filter or triage the event when the schedule asks for it, and publish a fire.

        `user_id` is the run's owner and `personal` says the event is theirs
        alone -- a row on an RLS table -- which the filter needs to know before
        it records a judgement about it.
        """
        if schedule is None:
            raise ValueError("schedule is required")
        if not schedule.is_active:
            return ScheduleEventOutcome(fired=False)
        # Before the filter, not only before publishing: the filter's decision
        # is filed under the event, which is what makes a redelivery read it
        # back instead of asking again.
        if not source_event_id:
            raise ValueError("source_event_id is required to process a schedule fire")
        if schedule.triage is not None:
            return await self._triage(
                schedule,
                payload=payload,
                user_id=user_id,
                metadata=metadata,
                source_event_id=source_event_id,
                personal=personal,
            )

        verdict: ScheduleFilterVerdict | None = None
        if schedule.filter_instruction:
            if self.filter_service is None:
                raise RuntimeError("Schedule filter adapter is not configured")
            # Every filter failure propagates, quota included. Which of them is
            # worth retrying is a policy question, and it is answered at the
            # task boundary in `handle_llm_filter_task` — this layer does not
            # know whether its caller can retry.
            verdict = await self.filter_service.filter_event(
                schedule=schedule,
                instruction=schedule.filter_instruction,
                output_schema=schedule.filter_output_schema,
                event_payload=payload,
                source_event_id=source_event_id,
                owner_id=user_id,
                personal=personal,
            )
            if not verdict.proceed:
                logger.debug("schedule.schedule_processor.s_filtered_out_llm.observed")
                return ScheduleEventOutcome(fired=False, verdict=verdict)

        await self.event_publisher.publish_schedule_fired(
            schedule=schedule,
            payload=payload,
            user_id=user_id,
            metadata=metadata,
            llm_output=verdict.output if verdict is not None else None,
            source_event_id=source_event_id,
        )
        return ScheduleEventOutcome(fired=True, verdict=verdict)

    async def _triage(
        self,
        schedule: ScheduleEntity,
        *,
        payload: Mapping[str, object],
        user_id: UUID,
        metadata: Mapping[str, object] | None,
        source_event_id: str,
        personal: bool,
    ) -> ScheduleEventOutcome:
        """Route the event by the schedule's decider: fire, hold, or skip it.

        Failures propagate as the filter's do, for the same reason: whether one
        is worth retrying is decided where the caller can retry.
        """
        if self.triage_service is None:
            raise RuntimeError("Schedule triage adapter is not configured")
        triaged = await self.triage_service.triage_event(
            schedule=schedule,
            event_payload=payload,
            source_event_id=source_event_id,
            owner_id=user_id,
            personal=personal,
        )
        if triaged.route is TriageRoute.IGNORE:
            skip = ScheduleFilterVerdict(
                proceed=False, decision_id=triaged.decision_id, output=triaged.output
            )
            return ScheduleEventOutcome(fired=False, verdict=skip, triage=triaged)
        if triaged.route is not TriageRoute.ACT:
            return ScheduleEventOutcome(fired=False, triage=triaged)
        await self.event_publisher.publish_schedule_fired(
            schedule=schedule,
            payload=dict(payload),
            user_id=user_id,
            metadata=dict(metadata) if metadata is not None else None,
            llm_output=dict(triaged.output),
            source_event_id=source_event_id,
        )
        return ScheduleEventOutcome(fired=True, triage=triaged)
