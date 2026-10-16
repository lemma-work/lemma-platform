"""``schedule.filter``: ask a schedule's LLM filter about one event, once.

The run row is claimed ``FILTERING`` -- unique on the event -- and committed
*before* the model is asked, and it names the work item that claimed it. So:

* a duplicate delivery of the event finds the row and does nothing;
* a replay of this item after a crash or a lost lease finds its own claim and
  does not ask again -- the call may already have been made and billed, so
  the event is dead-lettered as interrupted, for a person to retry by hand;
* a provider that refuses before answering (rate limited, unreachable)
  releases the claim and the item retries later, uncharged.

The verdict, the fire it allows and the item's deletion commit together.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID

from app.core.infrastructure.work import (
    DeadContext,
    ItemFailure,
    JobContext,
    Unavailable,
    UnavailableEvidence,
)
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.decisions.contracts import (
    DecisionInvalidError,
    DecisionLimitedError,
    DecisionUnavailableError,
)
from app.modules.schedule.domain.interfaces import FilterVerdict
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleRunStatus
from app.modules.schedule.repositories.fire_ledger import FireLedger
from app.modules.schedule.repositories.schedule_repository import ScheduleRepository
from app.modules.schedule.repositories.schedule_run_repository import (
    FILTER_NOT_CONFIGURED,
    FILTER_UNAVAILABLE,
)
from app.modules.schedule.services.run_outcome_service import ScheduleRunOutcomeService
from app.modules.schedule.services.schedule_processor import filter_instruction_for
from app.modules.schedule.services.trigger_fire import (
    TriggerEvent,
    fire_trigger,
    jsonable,
    start_after_filter,
    target_kind_of,
)
from app.modules.schedule.work.kinds import FilterEvent
from app.modules.usage.contracts import UsageLimitExceededError

logger = get_logger(__name__)

#: The filter's claim was found by the item that made it: the model may have
#: answered (and billed) before the crash. Not the schedule's fault.
FILTER_INTERRUPTED = "ScheduleFilterInterrupted"
_CLAIMED_BY = "filter_item_id"


class ScheduleFilter:
    """The model seam, so a test counts calls rather than patching a module."""

    async def judge(
        self, schedule: ScheduleEntity, event: FilterEvent
    ) -> FilterVerdict:
        from app.modules.schedule.infrastructure.adapters.decision_filter import (
            DecisionScheduleFilter,
        )

        assert schedule.filter_instruction is not None
        return await DecisionScheduleFilter().filter_event(
            instruction=filter_instruction_for(
                schedule.filter_instruction, event.metadata
            ),
            output_schema=schedule.filter_output_schema,
            event_payload=dict(event.payload),
            schedule=schedule,
        )


_filter: ScheduleFilter = ScheduleFilter()


def install_schedule_filter(judge: ScheduleFilter | None) -> None:
    """Tests replace the model; ``None`` restores the decision-backed one."""
    global _filter
    _filter = judge or ScheduleFilter()


async def run_schedule_filter(ctx: JobContext, payload: FilterEvent) -> None:
    claim = await _claim(ctx, payload)
    if claim is None:
        return
    schedule, run_id = claim
    try:
        verdict = await _filter.judge(schedule, payload)
    except DecisionLimitedError as exc:
        await _release(ctx, run_id)
        raise Unavailable(
            "decision provider rate limited",
            evidence=UnavailableEvidence.RATE_LIMITED,
            retry_after=timedelta(seconds=exc.retry_after_seconds),
        ) from exc
    except DecisionUnavailableError as exc:
        if exc.reason in ("token_limit", "not_configured"):
            await _dead_letter(
                ctx,
                payload,
                "ScheduleFilterEventTooLarge"
                if exc.reason == "token_limit"
                else FILTER_NOT_CONFIGURED,
            )
            return
        await _release(ctx, run_id)
        if exc.reason == "transport":
            raise Unavailable(
                "decision provider unreachable",
                evidence=UnavailableEvidence.CONNECTION_REFUSED,
            ) from exc
        raise ItemFailure(f"decision provider: {exc.reason}") from exc
    except (UsageLimitExceededError, DecisionInvalidError) as exc:
        # Retrying cannot help: the pod's budget is spent, or the request is
        # invalid as asked. Counted on the breaker.
        await _dead_letter(
            ctx,
            payload,
            "ScheduleFilterQuotaExhausted"
            if isinstance(exc, UsageLimitExceededError)
            else "ScheduleFilterInvalid",
        )
        return
    async with ctx.complete_work() as uow:
        current = await ScheduleRepository(uow=uow).get(payload.schedule_id)
        if current is None:
            return
        ledger = FireLedger(uow.session)
        if not verdict.proceed:
            await ledger.settle_skipped(run_id, llm_output=jsonable(verdict.output))
            return
        await start_after_filter(
            uow,
            current,
            run_id=run_id,
            source_event_id=payload.source_event_id,
            llm_output=jsonable(verdict.output),
            metadata=payload.metadata,
            work=ctx.writer(uow),
        )


async def _claim(
    ctx: JobContext, payload: FilterEvent
) -> tuple[ScheduleEntity, UUID] | None:
    """Claim the event FILTERING for this item, committed. ``None``: nothing to ask."""
    async with ctx.checkpoint() as uow:
        schedule = await ScheduleRepository(uow=uow).get(payload.schedule_id)
        if schedule is None:
            return None
        if not schedule.filter_instruction:
            # The filter was removed after the event was queued: it fires as an
            # unfiltered schedule would.
            await fire_trigger(uow, schedule, _event(payload), work=ctx.writer(uow))
            return None
        reservation = await FireLedger(uow.session).reserve(
            schedule_id=schedule.id,
            user_id=payload.user_id or schedule.user_id,
            source_event_id=payload.source_event_id,
            target_kind=target_kind_of(schedule),
            payload=payload.payload,
            metadata={**payload.metadata, _CLAIMED_BY: str(ctx.item.id)},
            llm_output=None,
            source_occurred_at=payload.source_occurred_at,
            status=ScheduleRunStatus.FILTERING,
        )
        if reservation is None:
            return None
        if reservation.created:
            return schedule, reservation.run_id
        if reservation.status != ScheduleRunStatus.FILTERING:
            return None  # already judged: fired, skipped or given up on
        claimed_by = await _claimant(uow, reservation.run_id)
    if claimed_by == str(ctx.item.id):
        logger.warning(
            "schedule.filter.interrupted",
            schedule_id=str(payload.schedule_id),
            pod_id=ctx.item.ns,
        )
        await _dead_letter(ctx, payload, FILTER_INTERRUPTED)
    return None


async def _claimant(uow: SqlAlchemyUnitOfWork, run_id: UUID) -> str | None:
    model = await FireLedger(uow.session).lock(run_id)
    if model is None:
        return None
    value = (model.fire_metadata or {}).get(_CLAIMED_BY)
    return value if isinstance(value, str) else None


async def _release(ctx: JobContext, run_id: UUID) -> None:
    async with ctx.checkpoint() as uow:
        await FireLedger(uow.session).release_filtering(run_id)


async def _dead_letter(ctx: JobContext, payload: FilterEvent, error_type: str) -> None:
    async with ctx.complete_work() as uow:
        await record_filter_dead_letter(uow, payload, error_type)


async def record_filter_dead_letter(
    uow: SqlAlchemyUnitOfWork, payload: FilterEvent, error_type: str
) -> None:
    schedule = await ScheduleRepository(uow=uow).get(payload.schedule_id)
    if schedule is None:
        return
    recorded = await ScheduleRunOutcomeService(uow).record_pre_dispatch_failure(
        schedule,
        source_event_id=payload.source_event_id,
        error_type=error_type,
        user_id=payload.user_id,
        payload=payload.payload,
        metadata=payload.metadata,
    )
    logger.warning(
        "schedule.filter.dead_lettered.degraded",
        schedule_id=str(schedule.id),
        pod_id=str(schedule.pod_id) if schedule.pod_id else None,
        error_type=error_type,
        counted=recorded,
    )


async def on_filter_dead(ctx: DeadContext) -> None:
    """Out of retries or of the unavailable budget: the event is dead-lettered."""
    try:
        payload = FilterEvent.model_validate(ctx.item.payload)
    except ValueError:
        return
    await record_filter_dead_letter(ctx.uow, payload, FILTER_UNAVAILABLE)


def _event(payload: FilterEvent) -> TriggerEvent:
    return TriggerEvent(
        source_event_id=payload.source_event_id,
        payload=payload.payload,
        metadata=payload.metadata,
        user_id=payload.user_id,
        source_occurred_at=payload.source_occurred_at,
    )
