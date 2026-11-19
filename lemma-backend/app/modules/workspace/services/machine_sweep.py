"""The sweep, as steps with budgets.

The worker runs it every ``WORKSPACE_SWEEP_INTERVAL_SECONDS`` under a
thirty-minute cap. It used to run its steps one after another with no limit
on any, slowest first, so a few machines that took their full timeouts to
answer a backup could hold off idle release and orphan reclaim -- the two
steps that stop money being spent -- until the cap killed the whole tick.

Now the cheap cost-control steps run first (meter, idle release, orphan
reclaim), every step has its own budget, and a step that overruns it or
fails is logged with its traceback and the next step runs anyway. The
budgets add up to less than the cap.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings

logger = get_logger(__name__)


def _step_failures() -> tuple[type[BaseException], ...]:
    """What one step can fail with and leave the rest to run: a machine, its
    provider, Redis or the database not answering, or a refusal."""
    from redis.exceptions import RedisError
    from sqlalchemy.exc import SQLAlchemyError

    from app.modules.workspace.providers.base import (
        ProviderFailed,
        ProviderGone,
        ProviderNotReady,
        ProviderRejected,
    )
    from app.modules.workspace.services.machine_housekeeping import (
        PER_MACHINE_FAILURES,
    )

    return (
        *PER_MACHINE_FAILURES,
        ProviderFailed,
        ProviderGone,
        ProviderNotReady,
        ProviderRejected,
        RedisError,
        SQLAlchemyError,
    )


@dataclass(frozen=True, slots=True)
class SweepStep:
    name: str
    budget: timedelta
    run: Callable[[], Awaitable[object]]


async def run_steps(steps: list[SweepStep]) -> list[str]:
    """Each step in order, inside its budget. Returns the steps that did not
    finish -- over budget or failed -- having logged each."""
    unfinished: list[str] = []
    for step in steps:
        try:
            async with asyncio.timeout(step.budget.total_seconds()):
                await step.run()
        except TimeoutError:
            unfinished.append(step.name)
            logger.warning(
                "workspace.machine_sweep.step_over_budget.degraded",
                step=step.name,
                budget_seconds=int(step.budget.total_seconds()),
            )
        except _step_failures() as exc:
            unfinished.append(step.name)
            logger.error(
                "workspace.machine_sweep.step_failed",
                step=step.name,
                error_type=type(exc).__name__,
                exc_info=True,
            )
    return unfinished


async def provider_stopped(service) -> frozenset[UUID]:
    """Sandboxes the provider reports as not running: on E2B, those it paused
    on its own timeout while their rows still say READY. Empty when the
    provider cannot be listed -- the meter then trusts the rows, as before."""
    deadline_at = datetime.now(timezone.utc) + timedelta(seconds=60)
    try:
        objects = await service._provider.list_objects(deadline_at=deadline_at)
    except _step_failures() as exc:
        logger.warning(
            "workspace.machine_sweep.provider_list_failed.degraded",
            error_type=type(exc).__name__,
            exc_info=True,
        )
        return frozenset()
    return frozenset(
        obj.sandbox_id
        for obj in objects
        if obj.sandbox_id is not None and obj.kind != "volume" and not obj.running
    )


@dataclass
class _Tick:
    running: list[UUID]
    resumed: list[UUID]


def sweep_steps(uow_factory: UnitOfWorkFactory, *, service, sweeper) -> list[SweepStep]:
    """The sweep's steps, cheapest cost control first."""
    from app.modules.workspace.services import compute_meter, machine_housekeeping

    tick = _Tick(running=[], resumed=[])

    async def meter() -> None:
        stopped = await provider_stopped(service)
        result = await compute_meter.meter_running(uow_factory, stopped=stopped)
        tick.running, tick.resumed = result.running, result.resumed

    async def release_idle() -> None:
        idle_after = workspace_settings.idle_release_seconds
        if idle_after <= 0:
            return
        released = await sweeper.release_idle(idle_after_seconds=idle_after)
        if released:
            logger.info(
                "workspace.sandbox_sweeper.released_idle_sandboxes.observed",
                released_count=released,
            )

    async def reclaim() -> None:
        reclaimed = await sweeper.reclaim_orphans()
        if reclaimed:
            logger.info(
                "workspace.sandbox_sweeper.reclaimed_orphaned_objects.observed",
                reclaimed_count=len(reclaimed),
            )

    async def resumed() -> None:
        if tick.resumed:
            await machine_housekeeping.after_resume(uow_factory, tick.resumed)

    async def enforce() -> None:
        await compute_meter.enforce(uow_factory)

    async def daily() -> None:
        await machine_housekeeping.daily_login_copies(uow_factory, tick.running)

    minutes = timedelta(minutes=1)
    return [
        # Metering before release, so a machine released this tick has its
        # last slice written by the release rather than skipped by both.
        SweepStep("meter", 3 * minutes, meter),
        SweepStep("release_idle", 8 * minutes, release_idle),
        SweepStep("reclaim_orphans", 4 * minutes, reclaim),
        SweepStep("after_resume", 5 * minutes, resumed),
        SweepStep("enforce", 4 * minutes, enforce),
        SweepStep("daily_login_copies", 4 * minutes, daily),
    ]


__all__ = ["SweepStep", "provider_stopped", "run_steps", "sweep_steps"]
