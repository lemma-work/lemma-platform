"""Background jobs for schedule processing.

Note: workflow module owns consumption of ``schedule_events`` stream for starting/resuming
workflow runs. Keeping an additional no-op subscriber here can cause nondeterministic
message consumption.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID
from faststream.redis import RedisRouter

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_runtime import streaq_task
from app.modules.schedule.domain.errors import ScheduleFilterUndecidedError
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.services.run_outcome_service import ScheduleRunOutcomeService
from app.modules.schedule.services.schedule_processor import ScheduleEventOutcome
from app.modules.schedule.services.triage_holds import ask_if_waiting
from app.modules.schedule.infrastructure.adapters.triage_questions import (
    NotificationTriageQuestions,
)
from pydantic_ai.exceptions import UsageLimitExceeded as PydanticAIUsageLimitExceeded

from app.modules.usage.contracts import UsageLimitExceededError
from app.core.log.log import get_logger
from app.modules.schedule.infrastructure.adapters.system_model_filter import (
    create_schedule_processor,
)

router = RedisRouter()
logger = get_logger(__name__)


@streaq_task(name="handle_llm_filter_task")
async def handle_llm_filter_task(
    payload: dict[str, Any],
    metadata: dict[str, Any],
    schedule_id: str | None = None,
    source_event_id: str | None = None,
) -> None:
    """Apply LLM filtering to a webhook event.

    Loads the schedule in a short-lived DB session, then runs the LLM filter
    and publishes the result with no DB session held — the LLM call can take
    tens of seconds and must not hold a pooled connection idle.

    Every outcome is recorded (PS-SCHED-012): a fire publishes and the target's
    module claims its run, a skip becomes a ``FILTERED`` run carrying its
    decision, and a filter that could not decide becomes a failed one. A
    schedule's triage runs here too, for the same reason, and its held events
    become ``HELD`` runs -- with a question to their person when one is owed.
    """
    if schedule_id is None:
        raise ValueError("schedule_id is required")
    if source_event_id is None:
        raise ValueError("source_event_id is required")

    uow_factory = SessionUnitOfWorkFactory(async_session_maker)

    async with uow_factory() as uow:
        schedule = await ScheduleRepository(uow=uow).get(UUID(schedule_id))

    if schedule is None:
        logger.debug(
            "schedule.filter.not_found",
            schedule_id=schedule_id,
        )
        return

    if not schedule.filter_instruction and schedule.triage is None:
        logger.debug(
            "schedule.schedule_consumer.s_has_no_filter_instruction.diagnostic",
            schedule_id=schedule_id,
        )
        return

    processor = create_schedule_processor()
    try:
        outcome = await processor.process_event(
            schedule=schedule,
            payload=payload,
            # Only webhook fires are deferred to this queue, and they carry no row
            # owner, so the schedule owner is the authoritative owner here.
            user_id=schedule.user_id,
            metadata=metadata,
            source_event_id=source_event_id,
        )
    except ScheduleFilterUndecidedError as exc:
        # Not re-raised: the decision is recorded under this event, so a retry
        # would read back the same open question. It ends here, as a failed
        # fire the breaker counts. A triage's failures are subclasses, each
        # with its own `error_type`.
        async with uow_factory() as uow:
            recorded = await ScheduleRunOutcomeService(uow).record_filter_undecided(
                schedule,
                source_event_id=source_event_id,
                decision_id=exc.decision_id,
                metadata=metadata,
                error_type=exc.error_type,
            )
        logger.warning(
            "schedule.schedule_consumer.filter_undecided.degraded",
            schedule_id=schedule_id,
            decision_id=str(exc.decision_id) if exc.decision_id else None,
            error_type=exc.error_type,
            counted=recorded,
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
        #
        # With the filter a decision, the pod's budget surfaces here from the
        # decision's model rung as well as from extraction. pydantic-ai's own
        # limit now comes only from extraction: the decision's model rung turns
        # an overrun into an open question, which is the branch above.
        async with uow_factory() as uow:
            recorded = await ScheduleRunOutcomeService(uow).record_pre_dispatch_failure(
                schedule,
                source_event_id=source_event_id,
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
            schedule_id=schedule_id,
            pod_id=str(schedule.pod_id) if schedule.pod_id else None,
            counted=recorded,
        )
    else:
        await _record_outcome(
            uow_factory,
            schedule,
            outcome,
            source_event_id=source_event_id,
            payload=payload,
            metadata=metadata,
        )


async def _record_outcome(
    uow_factory: SessionUnitOfWorkFactory,
    schedule: ScheduleEntity,
    outcome: ScheduleEventOutcome,
    *,
    source_event_id: str,
    payload: Mapping[str, object],
    metadata: Mapping[str, object],
) -> None:
    """Record the event as skipped, or as held, when that is what it came to.

    A fire needs nothing here: the target's module claims its run when the
    `schedule.fired` event reaches it. A held event's question goes out only
    once its hold has committed; see `triage_holds`.
    """
    verdict = outcome.filtered
    if verdict is not None:
        async with uow_factory() as uow:
            await ScheduleRunOutcomeService(uow).record_filtered(
                schedule,
                source_event_id=source_event_id,
                user_id=schedule.user_id,
                metadata=metadata,
                llm_output=verdict.output,
            )
        return
    held = outcome.held
    if held is None:
        return
    async with uow_factory() as uow:
        run = await ScheduleRunOutcomeService(uow).record_held(
            schedule,
            held,
            source_event_id=source_event_id,
            user_id=schedule.user_id,
            payload=payload,
            metadata=metadata,
        )
    await ask_if_waiting(NotificationTriageQuestions(), schedule, run, held)
