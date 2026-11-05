"""The sweep of Slack apps Lemma made that nobody authorised.

Apart from ``slack_app_provisioning`` because it is a job of its own, run by
``events/periodic_work``: it walks pending rows and asks Slack to delete what
nobody allowed, using the provisioner only for a fresh access token.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import SecretStr

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.errors import (
    AgentSurfaceError,
    SlackAppCreationNotSetUpError,
    SlackAppCreationTokenExpiredError,
)
from app.modules.agent_surfaces.infrastructure.repositories.slack_pending_app_binding_repository import (  # noqa: E501
    SlackPendingAppBindingRepository,
)
from app.modules.agent_surfaces.services.slack_app_provisioning import (
    OrgInstalls,
    SlackAppCalls,
    SlackAppProvisioner,
)

logger = get_logger(__name__)

#: How long an app Lemma made may wait for somebody to authorise it.
UNAUTHORISED_APP_KEPT = timedelta(days=7)
#: How long past ``UNAUTHORISED_APP_KEPT`` a sweep keeps retrying an app Slack
#: will not delete, before it lets the row go so it stops filling the batch.
UNDELETABLE_APP_RETRIED = timedelta(days=30)
_SWEEP_BATCH = 50


async def sweep_unauthorised_apps(
    uow_factory: UnitOfWorkFactory,
    *,
    calls: SlackAppCalls | None = None,
    installs: OrgInstalls | None = None,
    kept: timedelta = UNAUTHORISED_APP_KEPT,
) -> int:
    """Delete the Slack apps Lemma made that nobody authorised in ``kept``.

    An app whose install has an account was authorised: only its pending row
    goes. Otherwise the app goes from Slack, then its install (and the row with
    it). An app Slack would not delete keeps its row, so a later sweep asks
    again, for ``UNDELETABLE_APP_RETRIED``. A workspace whose token is gone or
    refused cannot be asked at all, so the install and row go and the app id is
    logged. Returns how many rows it settled.
    """
    installs = installs or OrgInstalls()
    async with uow_factory() as uow:
        rows = await SlackPendingAppBindingRepository(uow.session).made_before(
            datetime.now(UTC) - kept, limit=_SWEEP_BATCH
        )
        stale = [
            (
                row.id,
                row.organization_id,
                row.team_id,
                row.app_id,
                row.auth_config_id,
                row.created_at,
            )
            for row in rows
        ]
    settled = 0
    give_up_before = datetime.now(UTC) - kept - UNDELETABLE_APP_RETRIED
    for row_id, organization_id, team_id, app_id, auth_config_id, made_at in stale:
        async with uow_factory() as uow:
            if await installs.is_authorised(uow, auth_config_id):
                await SlackPendingAppBindingRepository(uow.session).delete(row_id)
                settled += 1
                continue
            provisioner = SlackAppProvisioner(uow, calls=calls, installs=installs)
            try:
                access_token: SecretStr | None = await provisioner.fresh_access_token(
                    organization_id, team_id
                )
            except (
                SlackAppCreationNotSetUpError,
                SlackAppCreationTokenExpiredError,
            ):
                access_token = None
            except AgentSurfaceError:
                # Slack is busy or failing; the next sweep tries again.
                logger.warning(
                    "agent_surfaces.slack_app_creation.sweep.degraded",
                    team_id=team_id,
                    app_id=app_id,
                    exc_info=True,
                )
                continue
            app_deleted = False
            if access_token is not None and app_id:
                async with connection_released(uow.session):
                    app_deleted = await provisioner.slack.delete_app(
                        access_token, app_id
                    )
                if not app_deleted and made_at >= give_up_before:
                    # Slack can still be asked: keep the row, so the next sweep
                    # tries again, rather than forget an app that is still there.
                    logger.warning(
                        "agent_surfaces.slack_app_creation.sweep.degraded",
                        team_id=team_id,
                        app_id=app_id,
                    )
                    continue
            await installs.delete_unauthorised(
                uow, organization_id=organization_id, auth_config_id=auth_config_id
            )
            await SlackPendingAppBindingRepository(uow.session).delete(row_id)
            settled += 1
            logger.info(
                "agent_surfaces.slack_app_creation.unauthorised_app_swept",
                organization_id=str(organization_id),
                team_id=team_id,
                app_id=app_id,
                app_deleted=app_deleted,
            )
    return settled
