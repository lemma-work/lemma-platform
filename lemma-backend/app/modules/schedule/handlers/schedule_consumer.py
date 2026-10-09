"""Background jobs for schedule processing.

Note: workflow module owns consumption of ``schedule_events`` stream for starting/resuming
workflow runs. Keeping an additional no-op subscriber here can cause nondeterministic
message consumption.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from faststream.redis import RedisRouter
from streaq import StreaqRetry

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_runtime import streaq_task
from app.core.log.log import get_logger
from app.core.request_context import current_observability_context
from app.modules.decisions.contracts import (
    DecisionInvalidError,
    DecisionLimitedError,
    DecisionUnavailableError,
)
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.infrastructure.adapters.decision_filter import (
    create_schedule_processor,
)
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.repositories.schedule_run_repository import (
    FILTER_NOT_CONFIGURED,
    FILTER_UNAVAILABLE,
    ScheduleRunRepository,
)
from app.modules.schedule.services.run_outcome_service import ScheduleRunOutcomeService
from app.modules.usage.contracts import UsageLimitExceededError

router = RedisRouter()
logger = get_logger(__name__)

#: Attempts at judging one event before it is given up as unavailable. Enough to
#: ride out a provider blip or a rate window; a longer outage is recorded rather
#: than left to retry forever.
FILTER_MAX_TRIES = 6
#: Seconds before each retry, by attempt. A rate limit's own Retry-After is
#: waited out when it is longer.
_BACKOFF_SECONDS = (10, 30, 120, 300, 600)


@streaq_task(name="handle_llm_filter_task", max_tries=FILTER_MAX_TRIES)
async def handle_llm_filter_task(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    schedule_id: str | None = None,
    source_event_id: str | None = None,
    user_id: str | None = None,
) -> None:
    """Judge an event against its schedule's filter, then fire or record the skip.

    Webhook events and table changes both come here, so a filter gets the same
    retries, dead letter and redelivery guard whichever way its event arrived,
    and no event stream waits on a model. `user_id` is the changed row's owner
    for a table change, whose authority the fire runs with (PS-SCHED-011); a
    webhook has none, and its schedule's owner runs it.

    Loads the schedule in a short-lived DB session, then asks the decision with
    no DB session held -- it can take seconds and must not hold a pooled
    connection idle -- and records the outcome in another short session.

    Every outcome leaves a row (PS-SCHED-012): a fire is dispatched and ledgered
    as usual, a skip is a FILTERED run carrying the filter's answers, and a
    failure is dead-lettered so the breaker can count it. A provider that does
    not answer is retried with backoff before it is given up.
    """
    if schedule_id is None:
        raise ValueError("schedule_id is required")
    if source_event_id is None:
        raise ValueError("source_event_id is required")

    uow_factory = SessionUnitOfWorkFactory(async_session_maker)

    async with uow_factory() as uow:
        schedule = await ScheduleRepository(uow=uow).get(UUID(schedule_id))
        # A redelivered event this schedule already judged -- fired, skipped or
        # given up on -- is not judged, and paid for, a second time.
        judged = schedule is not None and await ScheduleRunRepository(
            uow
        ).has_run_for_event(schedule_id=schedule.id, source_event_id=source_event_id)

    if schedule is None or not schedule.filter_instruction or judged:
        logger.debug(
            "schedule.schedule_consumer.filter_skipped.diagnostic",
            schedule_id=schedule_id,
            found=schedule is not None,
            judged=judged,
        )
        return

    owner = UUID(user_id) if user_id else schedule.user_id
    # Known and accepted: two deliveries of one event racing each other can
    # both pass the check above and both be judged, so the decision may be
    # billed twice. Only one row is kept -- the run is claimed per event, and a
    # second skip is ON CONFLICT DO NOTHING. The window is the length of one
    # decision, and closing it would mean holding a lock across that call.
    try:
        processed = await create_schedule_processor().process_event(
            schedule=schedule,
            payload=payload,
            user_id=owner,
            metadata=metadata,
            source_event_id=source_event_id,
        )
    except (DecisionUnavailableError, DecisionLimitedError) as exc:
        failure = _unretryable(exc)
        if failure is None and _attempt() < FILTER_MAX_TRIES:
            raise StreaqRetry(delay=_retry_delay(exc)) from exc
        await _dead_letter(
            uow_factory,
            schedule,
            source_event_id=source_event_id,
            error_type=failure or FILTER_UNAVAILABLE,
            user_id=owner,
            payload=payload,
            metadata=metadata,
        )
        return
    except (UsageLimitExceededError, DecisionInvalidError) as exc:
        # Retrying cannot help either of these: the pod's budget will still be
        # spent on the next attempt, and an invalid request stays invalid.
        await _dead_letter(
            uow_factory,
            schedule,
            source_event_id=source_event_id,
            error_type=(
                "ScheduleFilterQuotaExhausted"
                if isinstance(exc, UsageLimitExceededError)
                else "ScheduleFilterInvalid"
            ),
            user_id=owner,
            payload=payload,
            metadata=metadata,
        )
        return

    if processed.outcome == "filtered":
        async with uow_factory() as uow:
            await ScheduleRunOutcomeService(uow).record_filtered(
                schedule,
                source_event_id=source_event_id,
                user_id=owner,
                metadata=metadata,
                llm_output=processed.llm_output,
            )


def _unretryable(exc: DecisionUnavailableError | DecisionLimitedError) -> str | None:
    """The error to record at once for a failure retrying cannot fix, else None.

    An event too large for the decision's token budget will be too large again,
    and a deployment with no decision provider will still have none.
    """
    if isinstance(exc, DecisionUnavailableError):
        if exc.reason == "token_limit":
            return "ScheduleFilterEventTooLarge"
        if exc.reason == "not_configured":
            return FILTER_NOT_CONFIGURED
    return None


def _attempt() -> int:
    return current_observability_context().job_attempt or 1


def _retry_delay(exc: DecisionUnavailableError | DecisionLimitedError) -> int:
    attempt = _attempt()
    delay = _BACKOFF_SECONDS[min(attempt, len(_BACKOFF_SECONDS)) - 1]
    if isinstance(exc, DecisionLimitedError):
        delay = max(delay, exc.retry_after_seconds)
    return delay


async def _dead_letter(
    uow_factory: SessionUnitOfWorkFactory,
    schedule: ScheduleEntity,
    *,
    source_event_id: str,
    error_type: str,
    user_id: UUID,
    payload: Mapping[str, object],
    metadata: Mapping[str, object],
) -> None:
    """End the fire as dead-lettered, never FAILED.

    A FAILED run is picked up by run recovery and re-sent to its target -- past
    the filter that never let it through. Dead-lettered, the run ends here,
    the breaker counts it (unless it was an outage), and a person can still
    retry it by hand.
    """
    async with uow_factory() as uow:
        recorded = await ScheduleRunOutcomeService(uow).record_pre_dispatch_failure(
            schedule,
            source_event_id=source_event_id,
            error_type=error_type,
            user_id=user_id,
            payload=payload,
            metadata=metadata,
        )
    logger.warning(
        "schedule.filter.dead_lettered.degraded",
        schedule_id=str(schedule.id),
        pod_id=str(schedule.pod_id) if schedule.pod_id else None,
        error_type=error_type,
        counted=recorded,
    )
