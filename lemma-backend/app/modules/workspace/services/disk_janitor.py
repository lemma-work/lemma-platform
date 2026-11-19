"""Keeping a shared machine's disk usable.

An E2B machine's disk is fixed at what its template was built with, and a pod
machine running reviews, image builds and compose stacks fills it.

Two halves, kept apart on purpose:

- **Pruning** is a background job (``workspace.machine_disk_janitor``), never
  an agent's command. At ``MACHINE_DISK_PRUNE_FRACTION`` (75%) it prunes,
  cheapest loss first: finished runs' working directories (never a live
  run's, never one written in the last hour), then Docker's unused images
  and build cache, then package caches. Each machine has its own time budget,
  so one slow disk does not hold up the rest -- or anyone's run.
- **Refusing** is inline and cheap: a run's first command on a shared machine
  measures the disk once, and past ``MACHINE_DISK_REFUSE_FRACTION`` (90%) the
  run is refused with a sentence saying why -- better than a run that dies of
  ENOSPC halfway through an install.

It never touches a protected path. Every command it runs is one of the reset
scopes, and none of those names the browser's profile.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory

from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.services.machine_shell import (
    DISK_USAGE,
    Shell,
    parse_disk_usage,
    run_on_machine,
)

logger = get_logger(__name__)

#: In the order the janitor gives them up.
PRUNE_ORDER = ("runs", "docker", "caches")
#: The most one machine's prune may take before the janitor moves on.
MACHINE_BUDGET_SECONDS = 15 * 60


@dataclass(frozen=True, slots=True)
class DiskReport:
    used_bytes: int
    total_bytes: int

    @property
    def fraction(self) -> float:
        return self.used_bytes / self.total_bytes if self.total_bytes else 0.0


_LAST = "machine:disk:v1:{machine_id}"
_LAST_TTL_SECONDS = 24 * 3600


async def disk_report(machine_id: UUID, *, acting_user: UUID) -> DiskReport | None:
    outcome = await run_on_machine(
        machine_id, acting_user=acting_user, command=DISK_USAGE, timeout_seconds=30
    )
    parsed = parse_disk_usage(outcome.stdout)
    if parsed is None:
        return None
    report = DiskReport(used_bytes=parsed[0], total_bytes=parsed[1])
    await _remember(machine_id, report)
    return report


async def _remember(machine_id: UUID, report: DiskReport) -> None:
    """The last measurement, for screens and run contexts that must not wake a
    machine to ask."""
    from app.core.config import settings
    from app.core.infrastructure.redis.client import get_redis

    await get_redis(url=settings.redis_url).set(
        _LAST.format(machine_id=machine_id),
        f"{report.used_bytes} {report.total_bytes}",
        ex=_LAST_TTL_SECONDS,
    )


async def last_disk(machine_id: UUID) -> DiskReport | None:
    from app.core.config import settings
    from app.core.infrastructure.redis.client import get_redis

    raw = await get_redis(url=settings.redis_url).get(
        _LAST.format(machine_id=machine_id)
    )
    if raw is None:
        return None
    parsed = parse_disk_usage(raw if isinstance(raw, str) else bytes(raw).decode())
    return DiskReport(used_bytes=parsed[0], total_bytes=parsed[1]) if parsed else None


async def last_disk_fraction(machine_id: UUID) -> float | None:
    report = await last_disk(machine_id)
    return report.fraction if report else None


Measure = Callable[..., Awaitable[DiskReport | None]]


async def refuse_if_full(
    machine_id: UUID, *, acting_user: UUID, measure: Measure | None = None
) -> DiskReport | None:
    """The inline half: one measurement, and a refusal past the refuse line.
    Never prunes -- that is the janitor's, off the run's path."""
    report = await (measure or disk_report)(machine_id, acting_user=acting_user)
    if (
        report is not None
        and report.fraction >= workspace_settings.machine_disk_refuse_fraction
    ):
        raise MachineConflict(
            f"This machine's disk is {report.fraction:.0%} full, so it is not "
            "taking new runs. The janitor cleans up finished runs, Docker "
            "images and caches in the background every few minutes; try again "
            "shortly, free space with a reset, or ask a pod editor for a "
            "bigger machine.",
            code="MACHINE_DISK_FULL",
        )
    return report


async def prune(
    machine_id: UUID,
    *,
    acting_user: UUID,
    measure: Measure | None = None,
    shell: Shell | None = None,
    live: Callable[[UUID], Awaitable[frozenset[str]]] | None = None,
) -> DiskReport | None:
    """Prune if past the prune line, cheapest loss first, until under it.
    ``measure``, ``shell`` and ``live`` reach the machine and its slots."""
    from app.modules.workspace.services.machine_lifecycle import (
        slot_holder_runs,
        scope_command,
    )

    measure = measure or disk_report
    shell = shell or run_on_machine
    live = live or slot_holder_runs
    report = await measure(machine_id, acting_user=acting_user)
    if (
        report is None
        or report.fraction < workspace_settings.machine_disk_prune_fraction
    ):
        return report
    for scope in PRUNE_ORDER:
        command = scope_command(scope, keep=await live(machine_id))
        if command is None:
            continue
        await shell(
            machine_id, acting_user=acting_user, command=command, timeout_seconds=600
        )
        report = await measure(machine_id, acting_user=acting_user) or report
        logger.info(
            "workspace.disk_janitor.pruned.observed",
            sandbox_id=str(machine_id),
            scope=scope,
            fraction=round(report.fraction, 3),
        )
        if report.fraction < workspace_settings.machine_disk_prune_fraction:
            break
    return report


async def janitor_pass(uow_factory: UnitOfWorkFactory) -> int:
    """The background half: every running shared machine, each within its own
    budget. Returns how many were looked at."""
    from app.modules.workspace.contracts.runtime_errors import (
        machine_runtime_errors,
    )
    from app.modules.workspace.domain.sandbox import SandboxOwnerKind
    from app.modules.workspace.services.compute_meter import running_machines

    from redis.exceptions import RedisError

    seen = 0
    for machine in await running_machines(uow_factory):
        if machine.owner_kind is not SandboxOwnerKind.POD:
            continue
        seen += 1
        try:
            async with asyncio.timeout(MACHINE_BUDGET_SECONDS):
                await prune(
                    machine.sandbox_id,
                    acting_user=machine.created_by or machine.owner_id,
                )
        except (TimeoutError, OSError, RedisError, *machine_runtime_errors()) as exc:
            logger.warning(
                "workspace.disk_janitor.prune_failed.degraded",
                sandbox_id=str(machine.sandbox_id),
                error_type=type(exc).__name__,
                exc_info=True,
            )
    return seen
