"""The surfaces module's recurring work, as periodic work items.

* ``surfaces.onboarding_cleanup`` (1 min) -- expired sign-up content, then the
  rows. Was a ``while True`` background task in every worker.
* ``surfaces.web_visitors`` (1 h) -- what web visitors leave behind. Was the
  ``sweep_web_visitors`` cron.
* ``surfaces.retention`` (1 d) -- inbound rows past the redelivery window
  (ROUTED/DROPPED after 7 days, which covers Meta's 7-day retry; FAILED after
  30), message claims after 7 days, finished deliveries after 30.

Admin class: none of it is anybody waiting.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import (
    JobContext,
    WorkClass,
    WorkMode,
    WorkRegistry,
    periodic,
    registry as default_registry,
)
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.work_payloads import NoPayload
from app.modules.agent_surfaces.events.work_kinds import ONBOARDING_CLEANUP_PERIODIC
from app.modules.agent_surfaces.infrastructure.repositories.delivery_repository import (
    DeliveryRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.inbound_message_repository import (  # noqa: E501
    InboundMessageRepository,
    MessageClaimRepository,
)

logger = get_logger(__name__)

WEB_VISITORS_PERIODIC = "surfaces.web_visitors"
RETENTION_PERIODIC = "surfaces.retention"

#: Answered inbound rows are kept past the longest platform retry (Meta: 7 d).
INBOUND_KEPT = timedelta(days=7)
FAILED_INBOUND_KEPT = timedelta(days=30)
CLAIMS_KEPT = timedelta(days=7)
DELIVERIES_KEPT = timedelta(days=30)
_BATCH = 1000
_BATCHES = 20


def _sessions() -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


async def onboarding_cleanup(ctx: JobContext, payload: NoPayload) -> None:
    from app.modules.agent_surfaces.services.onboarding_cleanup import (
        purge_expired_onboarding,
    )

    del ctx, payload
    await purge_expired_onboarding(_sessions())


async def web_visitors(ctx: JobContext, payload: NoPayload) -> None:
    from app.modules.agent_surfaces.events.visitor_retention import (
        sweep_web_visitors,
    )

    del ctx, payload
    codes, sessions = await sweep_web_visitors(_sessions())
    logger.info(
        "agent_surfaces.tasks.sweep_web_visitors.observed",
        codes=codes,
        sessions=sessions,
    )


async def retention(ctx: JobContext, payload: NoPayload) -> None:
    del payload
    now = datetime.now(UTC)
    removed = {"inbound": 0, "claims": 0, "deliveries": 0}
    for _ in range(_BATCHES):
        ctx.raise_if_stopping()
        async with ctx.checkpoint() as uow:
            inbound = await InboundMessageRepository(uow.session).purge_finished(
                finished_before=now - INBOUND_KEPT,
                failed_before=now - FAILED_INBOUND_KEPT,
                limit=_BATCH,
            )
            claims = await MessageClaimRepository(uow.session).purge(
                before=now - CLAIMS_KEPT, limit=_BATCH
            )
            deliveries = await DeliveryRepository(uow.session).purge_finished(
                before=now - DELIVERIES_KEPT, limit=_BATCH
            )
        removed["inbound"] += inbound
        removed["claims"] += claims
        removed["deliveries"] += deliveries
        if max(inbound, claims, deliveries) < _BATCH:
            break
    logger.info(
        "agent_surfaces.tasks.surface_retention.observed",
        inbound=removed["inbound"],
        claims=removed["claims"],
        deliveries=removed["deliveries"],
    )


def register_periodic_work(registry: WorkRegistry) -> None:
    for kind, handler in (
        (ONBOARDING_CLEANUP_PERIODIC, onboarding_cleanup),
        (WEB_VISITORS_PERIODIC, web_visitors),
        (RETENTION_PERIODIC, retention),
    ):
        registry.handler(
            kind=kind, cls=WorkClass.ADMIN, mode=WorkMode.JOB, payload=NoPayload
        )(handler)


register_periodic_work(default_registry)
periodic(ONBOARDING_CLEANUP_PERIODIC, timedelta(minutes=1), ONBOARDING_CLEANUP_PERIODIC)
periodic(WEB_VISITORS_PERIODIC, timedelta(hours=1), WEB_VISITORS_PERIODIC)
periodic(RETENTION_PERIODIC, timedelta(days=1), RETENTION_PERIODIC)
