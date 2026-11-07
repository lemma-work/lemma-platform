"""Keeping a shared machine's disk usable.

An E2B machine's disk is fixed at what its template was built with, and a pod
machine running reviews, image builds and compose stacks fills it. At
``MACHINE_DISK_PRUNE_FRACTION`` (75%) the janitor prunes, cheapest loss
first: finished runs' working directories, then Docker's unused images and
build cache, then package caches. Past ``MACHINE_DISK_REFUSE_FRACTION`` (90%)
after pruning, a new run is refused with a sentence saying why -- better than
a run that dies of ENOSPC halfway through an install.

It never touches a protected path. Every command it runs is one of the reset
scopes, and none of those names the browser's profile.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.core.log.log import get_logger
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine_errors import MachineConflict
from app.modules.workspace.services.machine_shell import (
    DISK_USAGE,
    parse_disk_usage,
    run_on_machine,
)

logger = get_logger(__name__)

#: In the order the janitor gives them up.
PRUNE_ORDER = ("runs", "docker", "caches")


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


async def keep_room(machine_id: UUID, *, acting_user: UUID) -> DiskReport | None:
    """Prune if past the prune line; refuse if still past the refuse line."""
    from app.modules.workspace.services.machine_lifecycle import scope_command

    report = await disk_report(machine_id, acting_user=acting_user)
    if (
        report is None
        or report.fraction < workspace_settings.machine_disk_prune_fraction
    ):
        return report
    for scope in PRUNE_ORDER:
        command = scope_command(scope)
        if command is None:
            continue
        await run_on_machine(
            machine_id, acting_user=acting_user, command=command, timeout_seconds=600
        )
        report = await disk_report(machine_id, acting_user=acting_user) or report
        logger.info(
            "workspace.disk_janitor.pruned.observed",
            sandbox_id=str(machine_id),
            scope=scope,
            fraction=round(report.fraction, 3),
        )
        if report.fraction < workspace_settings.machine_disk_prune_fraction:
            break
    if report.fraction >= workspace_settings.machine_disk_refuse_fraction:
        raise MachineConflict(
            f"This machine's disk is {report.fraction:.0%} full even after "
            "cleaning up finished runs, Docker images and caches, so it is not "
            "taking new runs. Free space with a reset or ask a pod editor for "
            "a bigger machine.",
            code="MACHINE_DISK_FULL",
        )
    return report
