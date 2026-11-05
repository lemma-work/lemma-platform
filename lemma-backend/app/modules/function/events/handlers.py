"""Function module periodic work: run and revision retention.

Queued runs are work items now (``events/run_work.py``), and so are their
deadlines (``events/run_deadline.py``); nothing here starts or recovers a run.
"""

from __future__ import annotations

import time
from collections.abc import Coroutine
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any
from uuid import UUID

from app.core.infrastructure.db.session import get_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.work import periodic_job
from app.modules.function.config import function_settings, revision_settings
from app.modules.function.infrastructure.repositories import FunctionRunRepository
from app.core.log.log import get_logger

logger = get_logger(__name__)


def provide_uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(get_session_maker())


async def _guard_cron(task_name: str, body: Coroutine[Any, Any, None]) -> None:
    """Keep one failed tick from taking the schedule down with it.

    A cron that raises stops contributing until the next tick anyway, so the
    only thing an escaping exception buys is a less useful log line. Both crons
    in this module share this boundary so the module keeps exactly one broad
    catch rather than one per schedule.
    """

    try:
        await body
    except Exception:
        logger.error(
            "function.handlers.cron.failed",
            task_name=task_name,
            exc_info=True,
        )


@periodic_job("function.runs_prune", every=timedelta(hours=1))
async def prune_function_runs() -> None:
    """Reclaim finished runs past the retention window.

    Nothing removed a function run before this existed, and a run carries its
    input, its output and its captured logs -- so the table grew in bytes
    faster than in rows, and every query over it got slower for the life of the
    install.

    Batched and budgeted for the same reason as the event-delivery sweep: the
    first run against an install that has never pruned has a large backlog to
    work through, and it has to do that without monopolising the database or
    running into the next tick. Stopping early costs nothing -- the cutoff is
    recomputed on the next run and the remainder is still eligible.

    Offset off the hour so it does not start alongside the event-delivery
    sweep; both are delete-heavy on the same database.
    """

    await _guard_cron("prune_function_runs", _prune_function_runs())


async def _prune_function_runs() -> None:
    budget = function_settings.function_run_retention_budget_seconds
    if budget <= 0:
        return

    batch_size = function_settings.function_run_retention_batch_size
    cutoff = datetime.now(timezone.utc) - timedelta(
        days=function_settings.function_run_retention_days
    )
    uow_factory = provide_uow_factory()
    started = time.monotonic()
    removed = 0
    while True:
        async with uow_factory() as uow:
            batch = await FunctionRunRepository(uow).delete_terminal_before(
                cutoff=cutoff,
                batch_size=batch_size,
            )
        removed += batch
        if batch < batch_size or (time.monotonic() - started) >= budget:
            break
    if removed:
        logger.debug(
            "function.handlers.prune_function_runs.observed",
            deleted_count=removed,
        )


@periodic_job(
    "function.revisions_sweep",
    every=timedelta(
        seconds=revision_settings.function_revision_retention_interval_seconds
    ),
)
async def sweep_function_revisions() -> None:
    """Daily backstop for revisions whose retention window has passed.

    Pruning already runs inline on a code save, which is when a function's
    storage grows by an artifact. This catches what that structurally cannot: a
    function that stops being edited, whose surplus revisions would otherwise
    age forever with nothing re-evaluating them.

    Admin class: it is slow, bursty and touches object storage.
    """
    # Through the module's shared cron boundary, which is why this module keeps
    # exactly one broad catch rather than one per schedule.
    await _guard_cron("sweep_function_revisions", _sweep_revisions())


async def _sweep_revisions() -> None:
    if not revision_settings.function_revision_retention_enabled:
        return
    outcome = await _sweep_function_revisions(
        provide_uow_factory(),
        page_size=revision_settings.function_revision_retention_batch,
        budget_seconds=revision_settings.function_revision_retention_budget_seconds,
    )
    # Logged even on a no-op tick: "found nothing" and "frozen" looked the same
    # from outside, which is how a sweep stuck on the head of the table went
    # unnoticed.
    logger.info(
        "function.handlers.sweep_function_revisions.observed",
        examined=outcome.examined,
        pruned_functions=outcome.pruned_functions,
        pruned_revisions=outcome.pruned_revisions,
        failed=outcome.failed,
        truncated=outcome.truncated,
    )


@dataclass(frozen=True, slots=True)
class RevisionSweepOutcome:
    examined: int = 0
    pruned_functions: int = 0
    pruned_revisions: int = 0
    failed: int = 0
    truncated: bool = False


async def _prune_one_function(uow_factory: UnitOfWorkFactory, function_id: UUID) -> int:
    """Plan and stamp in one short unit of work, then delete the bytes outside
    it. Returns how many revisions lost their artifacts."""
    from app.modules.function.api.dependencies import build_function_service
    from app.modules.function.services.function_revision_retention import (
        FunctionRevisionRetention,
    )

    async with uow_factory() as uow:
        service = build_function_service(uow)
        function = await service.repository.get(function_id)
        if function is None:
            return 0
        retention = FunctionRevisionRetention(
            service.repository, service.storage_factory
        )
        plan = await retention.plan(function)
        await uow.commit()
    if plan.is_empty:
        return 0
    await retention.execute(plan)
    async with uow_factory() as completed_uow:
        await build_function_service(completed_uow).repository.mark_revisions_purged(
            plan.version_ids
        )
        await completed_uow.commit()
    return len(plan.revision_numbers)


async def _sweep_function_revisions(
    uow_factory: UnitOfWorkFactory,
    *,
    page_size: int,
    budget_seconds: float = 0.0,
    now: datetime | None = None,
    prune_one=_prune_one_function,
) -> RevisionSweepOutcome:
    """Drain the functions that could have a prunable revision.

    Was ``ORDER BY id LIMIT batch_size`` with no cursor and no filter, so every
    tick examined the same functions and the tail -- a function that stopped
    being edited, which is the only case this cron exists for -- was never
    reached. See :mod:`app.core.infrastructure.db.retention_candidates`.

    ``budget_seconds`` of 0 means unlimited.
    """
    from app.core.infrastructure.db.retention_candidates import (
        owners_with_prunable_versions,
    )
    from app.modules.function.infrastructure.models import FunctionRevisionModel
    from app.modules.function.services.function_revision_retention import (
        PRUNE_FAILURES,
        revision_retention_policy,
    )

    moment = now or datetime.now(timezone.utc)
    policy = revision_retention_policy()
    started = time.monotonic()
    after: UUID | None = None
    examined = pruned_functions = pruned_revisions = failed = 0
    truncated = False

    while True:
        async with uow_factory() as uow:
            page = list(
                (
                    await uow.session.execute(
                        owners_with_prunable_versions(
                            owner_column=FunctionRevisionModel.function_id,
                            created_at_column=FunctionRevisionModel.created_at,
                            pruned_at_column=FunctionRevisionModel.pruned_at,
                            purged_at_column=FunctionRevisionModel.purged_at,
                            policy=policy,
                            now=moment,
                            after=after,
                            limit=page_size,
                        )
                    )
                )
                .scalars()
                .all()
            )
        if not page:
            break
        after = page[-1]

        for function_id in page:
            examined += 1
            try:
                removed = await prune_one(uow_factory, function_id)
            except PRUNE_FAILURES:
                failed += 1
                logger.warning(
                    "function.handlers.sweep_function_revisions.skipped",
                    function_id=str(function_id),
                    exc_info=True,
                )
                continue
            if removed:
                pruned_functions += 1
                pruned_revisions += removed

        if len(page) < page_size:
            break
        if budget_seconds > 0 and (time.monotonic() - started) >= budget_seconds:
            truncated = True
            break

    return RevisionSweepOutcome(
        examined=examined,
        pruned_functions=pruned_functions,
        pruned_revisions=pruned_revisions,
        failed=failed,
        truncated=truncated,
    )
