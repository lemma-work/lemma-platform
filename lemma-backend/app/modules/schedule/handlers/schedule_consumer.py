"""Background jobs for schedule processing.

Note: workflow module owns consumption of ``schedule_events`` stream for starting/resuming
workflow runs. Keeping an additional no-op subscriber here can cause nondeterministic
message consumption.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import httpx
from faststream.redis import RedisRouter
from pydantic_ai.exceptions import UnexpectedModelBehavior
from sqlalchemy.exc import SQLAlchemyError
from streaq import StreaqRetry

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.jobs.streaq_runtime import streaq_task
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleFireStatus
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.datastore_event_handler import log_fire_latency
from app.modules.schedule.services.run_outcome_service import ScheduleRunOutcomeService
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from pydantic_ai.exceptions import UsageLimitExceeded as PydanticAIUsageLimitExceeded

from app.modules.usage.contracts import (
    ProviderAttemptsExhaustedError,
    UsageLimitExceededError,
)
from app.core.log.log import get_logger
from app.modules.schedule.infrastructure.adapters.system_model_filter import (
    create_schedule_processor,
)

router = RedisRouter()
logger = get_logger(__name__)

#: Filter failures a later attempt can get past. They are retried after
#: `FILTER_RETRY_DELAY` until the task's `max_tries` is spent.
#:
#: Needed because streaq retries only a task that raises `StreaqRetry`; any
#: other exception ends the job, and the event is not delivered again. Without
#: this, a provider outage would drop the fire for good.
#:
#: Closed on purpose: anything not named here fails on its first attempt. A
#: provider error the metered model declined to retry is one of those -- the
#: model reports it as `ModelHTTPError`, which is absent here for that reason.
_RETRYABLE_FILTER_FAILURES: tuple[type[Exception], ...] = (
    # The metered model retried the provider itself and ran out of attempts.
    ProviderAttemptsExhaustedError,
    # An answer that did not fit the schema; the filter allows one request
    # per run, so the next attempt is its only second chance.
    UnexpectedModelBehavior,
    httpx.TransportError,
    TimeoutError,
    ConnectionError,
    # The reads the filter makes before the model, or staging the fire after.
    SQLAlchemyError,
)
FILTER_RETRY_DELAY = timedelta(seconds=30)


class FilterTaskStore(Protocol):
    """The database work around one deferred filter, and nothing held between.

    Each call is its own short unit of work: the model call sits between the
    first and the rest, and a pooled connection must not wait idle across it.
    """

    async def get_schedule(self, schedule_id: UUID) -> ScheduleEntity | None: ...

    async def record_fire(
        self,
        schedule_id: UUID,
        *,
        status: ScheduleFireStatus,
        error: str | None = None,
    ) -> None: ...

    async def record_pre_dispatch_failure(
        self, schedule: ScheduleEntity, *, source_event_id: str, error_type: str
    ) -> bool: ...


class SqlFilterTaskStore:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None:
        self._uow_factory = uow_factory

    async def get_schedule(self, schedule_id: UUID) -> ScheduleEntity | None:
        async with self._uow_factory() as uow:
            return await ScheduleRepository(uow=uow).get(schedule_id)

    async def record_fire(
        self,
        schedule_id: UUID,
        *,
        status: ScheduleFireStatus,
        error: str | None = None,
    ) -> None:
        async with self._uow_factory() as uow:
            await ScheduleRepository(uow=uow).record_fire(
                schedule_id, status=status, error=error
            )

    async def record_pre_dispatch_failure(
        self, schedule: ScheduleEntity, *, source_event_id: str, error_type: str
    ) -> bool:
        async with self._uow_factory() as uow:
            return await ScheduleRunOutcomeService(uow).record_pre_dispatch_failure(
                schedule, source_event_id=source_event_id, error_type=error_type
            )


@dataclass(frozen=True, slots=True)
class DeferredFilter:
    """One event waiting on one schedule's filter.

    `user_id` is set only when the event has an owner of its own: the row
    owner of an RLS datastore event. Everything else -- every webhook, and a
    shared row -- runs as the schedule owner.
    """

    schedule_id: UUID
    payload: dict[str, object]
    metadata: dict[str, object]
    source_event_id: str
    user_id: UUID | None = None


@streaq_task(name="handle_llm_filter_task")
async def handle_llm_filter_task(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    schedule_id: str | None = None,
    source_event_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Apply a schedule's LLM filter to one event, and fire it if it passes.

    Webhook and datastore schedules both come here, so neither evaluates a
    model on the path that received the event.
    """
    if schedule_id is None:
        raise ValueError("schedule_id is required")
    if source_event_id is None:
        raise ValueError("source_event_id is required")

    await run_deferred_filter(
        DeferredFilter(
            schedule_id=UUID(schedule_id),
            payload=payload,
            metadata=metadata,
            source_event_id=source_event_id,
            user_id=UUID(user_id) if user_id else None,
        ),
        store=SqlFilterTaskStore(SessionUnitOfWorkFactory(async_session_maker)),
        processor=create_schedule_processor(),
    )


async def run_deferred_filter(
    job: DeferredFilter,
    *,
    store: FilterTaskStore,
    processor: ScheduleProcessor,
) -> None:
    """Load the schedule, run its filter, and publish the fire if it passes.

    No database session is held across the model call: the store opens one
    per call, and the call between them can take tens of seconds.
    """
    schedule = await store.get_schedule(job.schedule_id)

    if schedule is None:
        logger.debug(
            "schedule.filter.not_found",
            schedule_id=str(job.schedule_id),
        )
        return

    if not schedule.filter_instruction:
        logger.debug(
            "schedule.schedule_consumer.s_has_no_filter_instruction.diagnostic",
            schedule_id=str(job.schedule_id),
        )
        return

    try:
        fired = await processor.process_event(
            schedule=schedule,
            payload=job.payload,
            user_id=job.user_id or schedule.user_id,
            metadata=job.metadata,
            source_event_id=job.source_event_id,
        )
    except (UsageLimitExceededError, PydanticAIUsageLimitExceeded) as exc:
        # Two unrelated classes with nearly the same name, and only one of them
        # was caught. `UsageLimitExceededError` is ours -- the pod's billing
        # budget. `UsageLimitExceeded` is pydantic-ai's, raised when a run
        # exceeds `input_tokens_limit`, and it is the one production actually
        # hits -- routinely, every occurrence an event too large for the
        # filter's token budget. It matched nothing here, so it escaped
        # unhandled, no ledger row was written, the breaker counted nothing,
        # the schedule was never deactivated and the owner was never emailed.
        # The safety net below was real and simply never applied to the failure
        # that occurs.
        #
        # Policy lives here, at the task boundary, not in the processor. The
        # processor raises every filter failure alike and is right to — a
        # provider blip should still reach streaq and be retried. Only this one
        # is different, and only because retrying it cannot help: the pod's
        # budget will still be spent on the next attempt.
        #
        # Before this it escaped as an unhandled exception, so the job just
        # failed. The ledger is written at dispatch, and this fire never got
        # that far, so no row existed — and the breaker counts rows. Nothing
        # bounded it and nothing told the owner. Recording the failure gives the
        # breaker something to count: five of them deactivates the schedule and
        # emails the owner, which is the ceiling this needed.
        recorded = await store.record_pre_dispatch_failure(
            schedule,
            source_event_id=job.source_event_id,
            # Distinguished, because they need different fixes: one means
            # the pod is out of budget, the other means the triggering
            # event was too large for the filter to read.
            error_type=(
                "ScheduleFilterQuotaExhausted"
                if isinstance(exc, UsageLimitExceededError)
                else "ScheduleFilterEventTooLarge"
            ),
        )
        logger.warning(
            "schedule.filter.quota_exhausted.degraded",
            schedule_id=str(job.schedule_id),
            pod_id=str(schedule.pod_id) if schedule.pod_id else None,
            counted=recorded,
        )
        return
    except _RETRYABLE_FILTER_FAILURES as exc:
        # The type, not the message: `last_error` is shown to the schedule's
        # owner, and a provider's own wording is not ours to repeat.
        await _record_outcome(
            store,
            schedule.id,
            ScheduleFireStatus.ERROR,
            error=f"{type(exc).__name__}: schedule filter failed",
        )
        raise StreaqRetry(delay=FILTER_RETRY_DELAY) from exc

    if not fired:
        await _record_outcome(store, schedule.id, ScheduleFireStatus.FILTERED)
        return
    # A fire that passed is stamped TRIGGERED by the target when it starts the
    # run, with the run's id; stamping it here as well would only race that.
    occurred_at = _event_occurred_at(job.metadata)
    if occurred_at is not None:
        log_fire_latency(schedule.id, occurred_at, llm_filter=True)


async def _record_outcome(
    store: FilterTaskStore,
    schedule_id: UUID,
    status: ScheduleFireStatus,
    *,
    error: str | None = None,
) -> None:
    """Stamp what the filter decided onto the schedule, best-effort.

    The datastore handler stamps the fires it decides itself; a filtered one is
    decided here, so this is the only place that knows. Telemetry only, so a
    database that will not take it must not fail the job -- that would buy a
    second model call to learn the same answer.
    """
    try:
        await store.record_fire(schedule_id, status=status, error=error)
    except SQLAlchemyError:
        logger.warning(
            "schedule.filter.outcome_unrecorded.degraded",
            schedule_id=str(schedule_id),
            status=status.value,
        )


def _event_occurred_at(metadata: dict[str, object]) -> datetime | None:
    """When the write behind this event happened, if the event says.

    A datastore event carries it; a webhook delivery does not, and has nothing
    to measure a fire's latency from.
    """
    raw = metadata.get("event_occurred_at")
    if not isinstance(raw, str):
        return None
    try:
        occurred_at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return occurred_at if occurred_at.tzinfo is not None else None
