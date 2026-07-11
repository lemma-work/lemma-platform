"""Datastore event handler for matching events to DATASTORE schedules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID

from app.modules.datastore.domain.events import DatastoreRecordEvent
from app.modules.schedule.domain.schedule import ScheduleFireStatus, ScheduleType
from app.modules.schedule.domain.value_objects import parse_datastore_operation
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from app.core.log.log import get_logger

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

    async def handle_datastore_event(self, event: DatastoreRecordEvent) -> List[UUID]:
        """Handle a datastore record event and fire matching schedules."""
        logger.info(
            "Handling datastore event: %s on %s",
            event.operation.value,
            event.table_name,
        )

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
        }

        fired_schedule_ids: list[UUID] = []
        for schedule in schedules:
            # One bad schedule must not drop the event for the rest.
            try:
                fired = await self.schedule_processor.process_event(
                    schedule=schedule,
                    payload=event.payload or {},
                    metadata=metadata,
                    source_event_id=str(event.event_id),
                )
            except Exception as exc:
                logger.error(
                    "Failed to fire DATASTORE schedule %s for %s on %s "
                    "(record %s): %s",
                    schedule.id,
                    event.operation.value,
                    event.table_name,
                    event.record_id,
                    exc,
                )
                await self._record_fire(
                    schedule.id, status=ScheduleFireStatus.ERROR, error=str(exc)
                )
                raise

            latency_ms = int(
                (datetime.now(timezone.utc) - event.occurred_at).total_seconds() * 1000
            )
            logger.info(
                "schedule.fire.latency_ms",
                schedule_id=str(schedule.id),
                latency_ms=latency_ms,
                fired=fired,
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
            logger.exception(
                "Failed to record fire telemetry for schedule %s", schedule_id
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
            logger.warning(
                "Datastore event %s on %s (record %s) matched none of the %d "
                "active DATASTORE schedule(s) in pod %s — check their "
                "table_name/operations config.",
                event.operation.value,
                event.table_name,
                event.record_id,
                len(active),
                event.pod_id,
            )
        else:
            logger.info(
                "No matching DATASTORE schedules for %s on %s",
                event.operation.value,
                event.table_name,
            )
