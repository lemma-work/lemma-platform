"""Settling up for a run whose worker never came back.

`reconcile_orphaned_agent_runs` is the process that cleans up after a worker
that went away mid-run, and there are two things to settle. The *hold* the run
was carrying has to go back, or the allowance stays reserved until the window
rolls over and quietly shrinks that person's budget in the meantime. And the
*spend* has to be recorded, because the tokens were bought from the provider
whatever happened to the worker afterwards -- `PS-OPS-003` says a run is
recorded "however the run ended", and a SIGKILL is one of the ways it ends.

Both are claimed under a row lock rather than merely read. The worker may turn
out to be alive and finalize a moment later, and exactly one of the two must
bill: charging the same tokens twice is worse than the gap it would be closing.

Its own module rather than another forty lines in `handlers.py`, whose length is
ratcheted.
"""

from __future__ import annotations

from uuid import UUID

from app.core.log.log import get_logger
from app.modules.agent.domain.value_objects import AgentRunStatus, JsonObject

logger = get_logger(__name__)


async def settle_orphaned_run(uow, repo, agent_run_id: UUID) -> None:
    """Bill what an abandoned run spent, and give back what it was holding.

    Order matters. Spend is recorded *with* the hold, so recording settles both
    at once -- the reservation becomes the actual cost rather than being handed
    back and then charged again. Only a run that spent nothing falls through to
    a plain release.
    """
    accumulated = await repo.claim_accumulated_usage(agent_run_id=agent_run_id)
    reservation = await repo.claim_usage_reservation(agent_run_id=agent_run_id)
    if accumulated is None and reservation is None:
        return
    if await _bill(uow, repo, agent_run_id, accumulated, reservation):
        return
    await _release(uow, reservation)


async def _bill(
    uow,
    repo,
    agent_run_id: UUID,
    accumulated: JsonObject | None,
    reservation: JsonObject | None,
) -> bool:
    """Write the usage row for an abandoned run. False if there was nothing to write."""
    from app.modules.agent.domain.value_objects import AgentRunUsage
    from app.modules.agent.infrastructure.repositories.agent_run_reservations import (
        attribution_for_run,
        summed_attempts,
    )
    from app.modules.usage.contracts.execution import (
        UsageExecutionContext,
        build_usage_service,
    )

    total = summed_attempts(accumulated)
    if not _spent_anything(total):
        return False
    attribution = await attribution_for_run(uow.session, agent_run_id=agent_run_id)
    if attribution is None or attribution.user_id is None:
        # No row, or a run with nobody to attribute it to. Recording an
        # unattributable cost would put a figure in a report that no filter can
        # explain, which is worse than the gap.
        logger.warning(
            "agent.orphan_reservations.unattributable_spend.degraded",
            agent_run_id=str(agent_run_id),
        )
        return False

    await build_usage_service(uow).record_agent_run_usage(
        ctx=UsageExecutionContext(
            user_id=attribution.user_id,
            organization_id=attribution.organization_id,
            pod_id=attribution.pod_id,
            agent_id=attribution.agent_id,
            conversation_id=attribution.conversation_id,
            agent_run_id=agent_run_id,
            source_type="agent_run",
            source_id=str(agent_run_id),
        ),
        runtime_profile=attribution.runtime_profile,
        usage_data=AgentRunUsage(
            model_name=str(total.get("model_name") or "unknown"),
            usage_kind="llm",
            input_tokens=_count(total, "input_tokens"),
            output_tokens=_count(total, "output_tokens"),
            request_count=_count(total, "request_count"),
            tool_call_count=_count(total, "tool_call_count"),
            metadata={
                "cache_read_tokens": _count(total, "cache_read_tokens"),
                "cache_write_tokens": _count(total, "cache_write_tokens"),
                # So a reader can tell this row apart from one the run wrote for
                # itself: nobody watched this spend land, and it is being billed
                # after the fact from what the run left behind.
                "reconciled": True,
            },
        ),
        status=AgentRunStatus.FAILED.value,
        reservation=_reservation_from(reservation, attribution),
    )
    return True


async def _release(uow, reservation: JsonObject | None) -> None:
    from app.modules.usage.contracts.execution import build_usage_service

    if not reservation:
        return
    await build_usage_service(uow).release_reservation_handle(
        counter_ids=_counter_ids(reservation),
        amount_usd=float(reservation.get("amount_usd") or 0.0),
    )


def _reservation_from(reservation: JsonObject | None, attribution) -> object | None:
    from app.modules.usage.contracts import UsageReservation

    if not reservation:
        return None
    return UsageReservation(
        organization_id=attribution.organization_id,
        user_id=attribution.user_id,
        amount_usd=float(reservation.get("amount_usd") or 0.0),
        counter_ids=_counter_ids(reservation),
    )


def _counter_ids(reservation: JsonObject) -> list[UUID]:
    raw = reservation.get("counter_ids") or []
    return [UUID(str(counter_id)) for counter_id in raw]  # type: ignore[union-attr]


def _spent_anything(total: JsonObject) -> bool:
    return any(_count(total, field) > 0 for field in ("input_tokens", "output_tokens"))


def _count(total: JsonObject, field: str) -> int:
    value = total.get(field)
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))
