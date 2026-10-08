"""Datastore event handler for matching events to DATASTORE schedules."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import List
from uuid import UUID

from app.modules.datastore.domain.events import DatastoreRecordEvent
from app.modules.schedule.domain.interfaces import ScheduleFilterTaskQueue
from app.modules.schedule.domain.match_conditions import evaluate_match_conditions
from app.modules.schedule.domain.schedule import (
    ScheduleEntity,
    ScheduleFireStatus,
    ScheduleType,
)
from app.modules.schedule.domain.value_objects import (
    DatastoreOperation,
    parse_datastore_operation,
)
from app.modules.schedule.infrastructure.adapters.filter_task_queue import (
    StreaqScheduleFilterTaskQueue,
)
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from app.core.infrastructure.db.session_uow import commit_now
from app.core.log.log import get_logger
from app.core.origin import Origin, OriginKind, origin_scope

logger = get_logger(__name__)


def log_fire_latency(
    schedule_id: UUID, occurred_at: datetime, *, llm_filter: bool
) -> None:
    """How long after the write its schedule fired.

    At info, because this is the number a datastore trigger is judged by and
    the one that is otherwise invisible: a schedule that fires late looks
    exactly like one that fires on time, just later. `llm_filter` separates the
    fires that waited on a model from the ones that did not.
    """
    latency_ms = int((datetime.now(timezone.utc) - occurred_at).total_seconds() * 1000)
    logger.info(
        "schedule.fire.latency_ms",
        schedule_id=str(schedule_id),
        latency_ms=latency_ms,
        llm_filter=llm_filter,
    )


class DatastoreEventHandler:
    """Match a datastore event to DATASTORE schedules and fire them.

    Database work only, by design. This runs on the one consumer group every
    pod's record events share, one message at a time, so anything slow here
    is slow for every pod on the platform. A schedule with a
    `filter_instruction` is therefore handed to the filter task -- the same
    one webhook schedules use -- rather than evaluated here: a model call
    here would hold back every other pod's triggers for as long as it took.
    """

    def __init__(
        self,
        schedule_repository: ScheduleRepository,
        schedule_processor: ScheduleProcessor,
        filter_task_queue: ScheduleFilterTaskQueue | None = None,
    ):
        self.schedule_repository = schedule_repository
        self.schedule_processor = schedule_processor
        self.filter_task_queue = filter_task_queue or StreaqScheduleFilterTaskQueue()

    async def handle_datastore_event(
        self,
        event: DatastoreRecordEvent,
    ) -> List[UUID]:
        """Handle a datastore record event and fire matching schedules.

        The connection is handed back before each schedule is processed; see the
        comment in the loop. That used to be an optional ``release`` callback the
        consumer passed and every test omitted, so the release had no coverage
        and the static gate could not see it either. It is taken from the
        repository's own unit of work now, which is the same object the consumer
        was passing.
        """

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

            # Let the connection go before the schedule is handed on. Either
            # way out leaves this session -- a Redis enqueue for a filtered
            # schedule, an outbox write on a session of its own otherwise -- and
            # a pooled connection must not sit idle across either.
            #
            # A commit rather than `connection_released`: a previous iteration
            # may have written a FILTERED fire row, and `safe_to_release`
            # correctly refuses a dirty session -- the release would be a
            # silent no-op exactly when the loop is longest.
            await commit_now(self.schedule_repository)

            # One bad schedule must not drop the event for the rest.
            try:
                # Deliberately *overrides* the inbound event's origin. The row
                # may well have been written from the web, but that is how the
                # write arrived -- this schedule's work arrived because a table
                # changed, and DATA_TRIGGER is the honest answer for everything
                # raised from here down. The filter task inherits it: origin
                # travels with an enqueued job.
                with origin_scope(Origin(OriginKind.DATA_TRIGGER)):
                    if schedule.filter_instruction:
                        await self._queue_filter(schedule, event, metadata)
                        # The filter task decides, and records what it decided.
                        continue
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

            log_fire_latency(schedule.id, event.occurred_at, llm_filter=False)
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

    async def _queue_filter(
        self,
        schedule: ScheduleEntity,
        event: DatastoreRecordEvent,
        metadata: dict[str, object],
    ) -> None:
        """Hand the schedule's filter to the filter task, keyed by this event.

        The event id is the whole of the idempotency: the job is keyed on
        schedule plus event, and the run it may start is claimed once per
        schedule plus event, so a redelivery that queues the job a second time
        still starts one run.
        """
        await self.filter_task_queue.enqueue(
            schedule_id=schedule.id,
            payload=event.payload or {},
            metadata=metadata,
            source_event_id=str(event.event_id),
            # `None` for a shared row, which has no owner of its own; the task
            # then runs it as the schedule owner, as a direct fire does.
            user_id=event.owner_user_id,
        )

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
        if event.payload_truncated:
            # The body was too large to carry, so the conditions cannot be
            # evaluated against it. Firing on an empty payload would match the
            # wrong rows in both directions, so the event is dropped and said
            # out loud -- a schedule that is silently 4 days behind is the
            # failure this codebase has already been bitten by. The durable fix
            # is to re-read the row here; until then this is visible, not quiet.
            logger.warning(
                "schedule.datastore_event_handler.truncated_payload.degraded",
                schedule_id=str(schedule.id),
                table_name=event.table_name,
                record_id=event.record_id,
            )
            return False
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
            # Degraded, not failed: this lookup only decides the severity of the
            # unmatched-event record below, so nothing user-facing breaks. But
            # "no schedules are configured" and "the database is down" must not
            # render as the same sentence, which is what an empty list did.
            logger.warning(
                "schedule.datastore_event_handler.active_schedule_lookup.degraded",
                pod_id=str(event.pod_id),
                exc_info=True,
            )
            active = []
        if active:
            logger.debug(
                "schedule.datastore_event_handler.datastore_event_s_s_record.diagnostic",
                record_id=event.record_id,
                count=len(active),
                pod_id=event.pod_id,
            )
