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
    if not apps_settings.app_release_retention_enabled:
        return
    try:
        pruned = await _sweep(
            _uow_factory(), batch_size=apps_settings.app_release_retention_batch
        )
        if pruned:
            logger.info(
                "apps.tasks.sweep_app_releases.observed",
                pruned_app_count=pruned,
            )
    except Exception:
        # Swallowed at the cron boundary so one bad tick does not stop the next.
        logger.error("apps.tasks.sweep_app_releases.failed", exc_info=True)


async def _sweep(uow_factory: UnitOfWorkFactory, *, batch_size: int) -> int:
    """Prune one bounded batch of apps. Extracted from the cron so it can be
    driven directly in tests with a fake factory."""
    from sqlalchemy import select

    from app.composition.pod_bundle_apps import build_app_service
    from app.modules.apps.infrastructure.models import AppModel
    from app.modules.apps.services.app_release_retention import AppReleaseRetention

    async with uow_factory() as uow:
        # Apps with the most releases first: they are where the bytes are, and a
        # bounded tick should spend its budget on them rather than on apps with
        # one release that can never be prunable.
        app_ids = list(
            (
                await uow.session.execute(
                    select(AppModel.id).order_by(AppModel.id).limit(batch_size)
                )
            )
            .scalars()
            .all()
        )

    pruned_apps = 0
    for app_id in app_ids:
        # One short unit of work per app, then its storage deletes outside it --
        # a connection is never held across object deletion, and a single bad
        # app cannot abort the sweep.
        try:
            async with uow_factory() as uow:
                service = build_app_service(uow)
                app = await service.repository.get(app_id)
                if app is None:
                    continue
                retention = AppReleaseRetention(
                    service.repository, service.file_manager_factory
                )
                plan = await retention.plan(app)
                await uow.commit()
            if plan.is_empty:
                continue
            await retention.execute(plan)
            pruned_apps += 1
        except Exception:
            logger.warning(
                "apps.tasks.sweep_app_releases.skipped",
                app_id=str(app_id),
                exc_info=True,
            )
    return pruned_apps
