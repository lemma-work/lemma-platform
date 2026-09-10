"""Daily sweep for app releases whose retention window has passed.

Pruning already happens inline when an app is deployed, which is when its
storage actually grows -- that covers the common case with no scheduled work at
all. This is the backstop for what inline pruning structurally cannot catch: an
app that STOPS being deployed. Its surplus releases keep ageing, and with
nothing re-evaluating them they would sit there forever.

It runs on the worker's bulk lane because it is slow, bursty and touches object
storage, so it must not compete with a user waiting on a request.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timezone
from uuid import UUID

from app.core.infrastructure.db.session import get_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
)
from app.core.infrastructure.jobs.streaq_runtime import Lane, streaq_cron
from app.modules.apps.config import apps_settings
from app.core.log.log import get_logger

logger = get_logger(__name__)


def _uow_factory() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(get_session_maker())


@streaq_cron(
    apps_settings.app_release_retention_cron,
    name="sweep_app_releases",
    lane=Lane.BULK,
)
async def sweep_app_releases() -> None:
    from app.modules.apps.services.app_release_retention import PRUNE_FAILURES

    if not apps_settings.app_release_retention_enabled:
        return
    try:
        outcome = await _sweep(
            _uow_factory(),
            page_size=apps_settings.app_release_retention_batch,
            budget_seconds=apps_settings.app_release_retention_budget_seconds,
        )
        # Logged even when it did nothing. "Found nothing" and "frozen" used to
        # look identical from outside, which is how a sweep that only ever
        # examined the same rows went unnoticed.
        logger.info(
            "apps.tasks.sweep_app_releases.observed",
            examined=outcome.examined,
            pruned_apps=outcome.pruned_apps,
            pruned_releases=outcome.pruned_releases,
            failed=outcome.failed,
            truncated=outcome.truncated,
        )
    except PRUNE_FAILURES:
        # The ways a sweep genuinely fails, swallowed so one bad tick does not
        # stop the next. Anything else is a defect and propagates to the worker,
        # which is louder than a "degraded" line nobody reads.
        logger.error("apps.tasks.sweep_app_releases.failed", exc_info=True)


@dataclass(frozen=True, slots=True)
class ReleaseSweepOutcome:
    """What a tick did. ``examined`` high while ``pruned_apps`` stays flat is the
    shape that would mean the candidate filter has started returning owners the
    rule always spares."""

    examined: int = 0
    pruned_apps: int = 0
    pruned_releases: int = 0
    failed: int = 0
    truncated: bool = False


async def _prune_one_app(uow_factory: UnitOfWorkFactory, app_id: UUID) -> int:
    """Plan and stamp in one short unit of work, then delete the bytes outside
    it. Returns how many releases lost their bytes."""
    from app.modules.apps.api.dependencies import build_app_service
    from app.modules.apps.services.app_release_retention import AppReleaseRetention

    async with uow_factory() as uow:
        service = build_app_service(uow)
        app = await service.repository.get(app_id)
        if app is None:
            return 0
        retention = AppReleaseRetention(
            service.repository, service.file_manager_factory
        )
        plan = await retention.plan(app)
        await uow.commit()
    if plan.is_empty:
        return 0
    await retention.execute(plan)
    async with uow_factory() as completed_uow:
        await build_app_service(completed_uow).repository.mark_releases_purged(
            plan.version_ids
        )
        await completed_uow.commit()
    return len(plan.release_numbers)


async def _sweep(
    uow_factory: UnitOfWorkFactory,
    *,
    page_size: int,
    budget_seconds: float = 0.0,
    now: datetime | None = None,
    prune_one=_prune_one_app,
) -> ReleaseSweepOutcome:
    """Drain the apps that could have a prunable release.

    Not one batch: a page at a time until the page comes back short. The old
    query took the lowest ``batch_size`` ids with no cursor and no filter, so
    every tick examined the same apps forever and an app that stopped being
    deployed -- the only case this cron exists for -- was never reached unless it
    happened to sort near the front.

    ``budget_seconds`` of 0 means unlimited, which is the opposite of the
    schedule-run drain's convention and deliberate: here the drain IS the fix, so
    the default must not be to stop after one page.

    ``prune_one`` is the test seam; both this and its function twin were split
    out so a sweep can be driven with a fake factory.
    """
    from app.modules.apps.services.app_release_retention import PRUNE_FAILURES

    from app.core.infrastructure.db.retention_candidates import (
        owners_with_prunable_versions,
    )
    from app.modules.apps.infrastructure.models import AppReleaseModel
    from app.modules.apps.services.app_release_retention import release_retention_policy

    moment = now or datetime.now(timezone.utc)
    policy = release_retention_policy()
    started = time.monotonic()
    after: UUID | None = None
    examined = pruned_apps = pruned_releases = failed = 0
    truncated = False

    while True:
        async with uow_factory() as uow:
            page = list(
                (
                    await uow.session.execute(
                        owners_with_prunable_versions(
                            owner_column=AppReleaseModel.app_id,
                            created_at_column=AppReleaseModel.created_at,
                            pruned_at_column=AppReleaseModel.pruned_at,
                            purged_at_column=AppReleaseModel.purged_at,
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
        # Keyset, not offset: rows leave the candidate set as they are pruned, so
        # an offset would step over apps it never looked at.
        after = page[-1]

        for app_id in page:
            examined += 1
            try:
                removed = await prune_one(uow_factory, app_id)
            except PRUNE_FAILURES:
                failed += 1
                logger.warning(
                    "apps.tasks.sweep_app_releases.skipped",
                    app_id=str(app_id),
                    exc_info=True,
                )
                continue
            if removed:
                pruned_apps += 1
                pruned_releases += removed

        if len(page) < page_size:
            break
        if budget_seconds > 0 and (time.monotonic() - started) >= budget_seconds:
            truncated = True
            break

    return ReleaseSweepOutcome(
        examined=examined,
        pruned_apps=pruned_apps,
        pruned_releases=pruned_releases,
        failed=failed,
        truncated=truncated,
    )
