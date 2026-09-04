"""Handing back the spend a dead worker was holding.

`reconcile_orphaned_agent_runs` is the process that cleans up after a worker
that went away mid-run. Until the reservation handle was persisted on the run's
row it had nothing to give back here, so the allowance stayed reserved until the
whole window rolled over -- permanently shrinking that person's budget in the
meantime.

Its own module rather than another twenty lines in `handlers.py`, whose length
is ratcheted.
"""

from __future__ import annotations

from uuid import UUID


async def release_orphaned_reservation(uow, repo, agent_run_id: UUID) -> None:
    """Give back the spend reservation a dead worker was holding.

    Claimed rather than simply read: if the worker turns out to be alive after
    all and finalizes a moment later, exactly one of the two releases the
    reservation, because releasing it twice would return allowance that was only
    ever taken once.
    """
    from app.modules.usage.contracts.execution import build_usage_service

    reservation = await repo.claim_usage_reservation(agent_run_id=agent_run_id)
    if not reservation:
        return
    await build_usage_service(uow).release_reservation_handle(
        counter_ids=[
            UUID(str(counter_id)) for counter_id in reservation.get("counter_ids") or []
        ],
        amount_usd=float(reservation.get("amount_usd") or 0.0),
    )
