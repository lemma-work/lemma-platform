"""Datastore event handler for matching events to DATASTORE schedules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID

from app.modules.datastore.domain.events import DatastoreRecordEvent
from app.modules.schedule.domain.match_conditions import evaluate_match_conditions
from app.modules.schedule.domain.schedule import ScheduleFireStatus, ScheduleType
from app.modules.schedule.domain.value_objects import (
    DatastoreOperation,
    parse_datastore_operation,
)
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from app.core.log.log import get_logger
from app.core.origin import Origin, OriginKind, origin_scope
from collections.abc import Awaitable, Callable

logger = get_logger(__name__)


class DatastoreEventHandler:
    """Handler that matches datastore events to DATASTORE schedules and fires them."""

    def __init__(
        self,
        schedule_repository: ScheduleRepository,
        schedule_processor: ScheduleProcessor,
    ):
        self.schedule_repository = schedule_repository
        self.schedule_processor = schedule_processor

    async def handle_datastore_event(
        self,
        event: DatastoreRecordEvent,
        *,
        release: Callable[[], Awaitable[None]] | None = None,
    ) -> List[UUID]:
        """Handle a datastore record event and fire matching schedules."""

        # Bridge datastore's record operation (lowercase) to schedule's
        # DatastoreOperation used for matching.
        operation = parse_datastore_operation(event.operation.value)

        schedules = await self.schedule_repository.find_by_pod_table_event(
            pod_id=event.pod_id,
            table_name=event.table_name,
            operation=operation,
        )
        if not schedules:
            await self._log_unmatched_event(event)
            return []

        metadata = {
            "table_name": event.table_name,
            "record_id": event.record_id,
            "operation": operation.value,
            "event_occurred_at": event.occurred_at.isoformat(),
            # Exposed so a workflow can bind to what the write actually did,
            # not just to the row it left behind.
            "changed": event.changed or [],
            "previous": event.previous or {},
        }

        fired_schedule_ids: list[UUID] = []
        for schedule in schedules:
            if not self._matches_conditions(schedule, event, operation):
                await self._record_fire(schedule.id, status=ScheduleFireStatus.FILTERED)
                continue

            # Let the connection go before processing. A schedule carrying a
            # filter_instruction runs an LLM inference inline here, once per
            # matching schedule, and holding the transaction across that keeps
            # a pooled connection idle for the length of every call in the
            # loop. The webhook sibling (schedule_consumer) already does this
            # and says why in its docstring.
            if release is not None:
                await release()

            # One bad schedule must not drop the event for the rest.
            try:
                # Deliberately *overrides* the inbound event's origin. The row
                # may well have been written from the web, but that is how the
                # write arrived -- this schedule's work arrived because a table
                # changed, and DATA_TRIGGER is the honest answer for everything
                # raised from here down.
                with origin_scope(Origin(OriginKind.DATA_TRIGGER)):
                    fired = await self.schedule_processor.process_event(
                        schedule=schedule,
                        payload=event.payload or {},
                        user_id=event.owner_user_id or schedule.user_id,
                        metadata=metadata,
                        source_event_id=str(event.event_id),
                    )
            except Exception as exc:
                logger.debug(
                    "schedule.datastore_event_handler.fire_datastore_schedule_s_s.propagated",
                    record_id=event.record_id,
                    exc_info=True,
                )
                await self._record_fire(
                    schedule.id, status=ScheduleFireStatus.ERROR, error=str(exc)
                )
                raise

            latency_ms = int(
                (datetime.now(timezone.utc) - event.occurred_at).total_seconds() * 1000
            )
            logger.debug(
                "schedule.fire.latency_ms",
                schedule_id=str(schedule.id),
                latency_ms=latency_ms,
            )
            await self._record_fire(
                schedule.id,
                status=(
                    ScheduleFireStatus.TRIGGERED
                    if fired
                    else ScheduleFireStatus.FILTERED
                ),
            )
            if fired:
                fired_schedule_ids.append(schedule.id)

        return fired_schedule_ids

    def _matches_conditions(
        self,
        schedule,
        event: DatastoreRecordEvent,
        operation: DatastoreOperation,
    ) -> bool:
        """Decide the cheap part of "should this fire" before anything costly.

        This runs ahead of the schedule's LLM filter deliberately: a condition
        that rules the event out here saves a model call on every write to the
        watched table, which is the whole reason to prefer one.
        """
        try:
            config = schedule.datastore_config
        except ValueError:
            # A config that no longer parses cannot be matched against. The
            # repository skips these when selecting, so reaching here means the
            # row changed underneath us; dropping the event is safer than
            # firing a schedule whose conditions are unreadable.
            logger.debug(
                "schedule.datastore_event_handler.unparsable_config.diagnostic",
                schedule_id=str(schedule.id),
            )
            return False
        if config is None or not config.when:
            return True
        return evaluate_match_conditions(
            config.when,
            operation=operation,
            payload=event.payload,
            changed=event.changed,
            previous=event.previous,
        )

    async def _record_fire(
        self,
        schedule_id: UUID,
        *,
        status: ScheduleFireStatus,
        error: str | None = None,
    ) -> None:
        try:
            await self.schedule_repository.record_fire(
                schedule_id, status=status, error=error
            )
        except Exception:
            logger.debug(
                "schedule.fire_telemetry.failed",
                schedule_id=schedule_id,
                exc_info=True,
            )

    async def _log_unmatched_event(self, event: DatastoreRecordEvent) -> None:
        """An event with no match is the signature of a misconfigured schedule.

        When active DATASTORE schedules exist for this pod but none matched,
        escalate to warning so the drop is visible.
        """
        try:
            active, _ = await self.schedule_repository.list(
                schedule_type=ScheduleType.DATASTORE,
                is_active=True,
                pod_id=event.pod_id,
            )
        except Exception:
            active = []
        if active:
            logger.debug(
                "schedule.datastore_event_handler.datastore_event_s_s_record.diagnostic",
                record_id=event.record_id,
                count=len(active),
                pod_id=event.pod_id,
            )
