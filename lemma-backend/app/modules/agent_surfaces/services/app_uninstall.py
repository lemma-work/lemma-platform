"""Stand a surface's account down when the platform takes its app away.

Slack sends ``app_uninstalled`` (or ``tokens_revoked`` for the bot) and then
nothing: every later send is refused, and until this ran the surface still read
"Connected and working". Marking the account ``REAUTH_REQUIRED`` is what the
rest of Lemma already understands -- the surface's setup turns into "Reinstall
it in Slack", and reconnecting repairs the account in place.

Scoped by the receivers the signature check resolved, never by the workspace:
one Slack workspace holds Lemma's app and any number of an org's own under the
same team id, and only the app that was removed signed this event.
"""

from __future__ import annotations

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfaceIngressRequest,
)
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort
from app.modules.agent_surfaces.domain.surface_connectors import (
    SURFACE_CONNECTOR_BINDINGS,
)
from app.modules.connectors.contracts.retirement import retire_accounts

logger = get_logger(__name__)


async def retire_uninstalled_app(
    uow: SqlAlchemyUnitOfWork,
    *,
    surfaces_repo: SurfaceInstallationRepositoryPort,
    request: SurfaceIngressRequest,
    parsed: ParsedSurfaceLifecycleEvent,
) -> int:
    """Mark the accounts the departed app's bot ran on. Returns how many changed."""
    if parsed.kind is not SurfaceLifecycleKind.UNINSTALLED:
        return 0
    binding = SURFACE_CONNECTOR_BINDINGS.get(parsed.platform)
    if isinstance(request, SurfaceDirectWebhookIngress):
        receiver_ids = [request.surface_id]
    else:
        receiver_ids = request.receiver_surface_ids
    # No verified receivers means no proof which app this is: a fan-in would
    # stand down every app in the workspace for one app's departure.
    if binding is None or not receiver_ids:
        logger.info(
            "agent_surfaces.app_uninstalled.unscoped",
            platform=parsed.platform.value,
            tenant_id=parsed.tenant_id,
        )
        return 0
    # By id, not through the routing read: that one is live surfaces only,
    # and a surface paused when its app was removed is one somebody will turn
    # back on -- to an app that is gone, still reading as connected.
    surfaces = [
        surface
        for surface in [
            await surfaces_repo.get(surface_id) for surface_id in receiver_ids
        ]
        if surface is not None
        and surface.surface_type is parsed.platform
        and surface.external_workspace_id == parsed.tenant_id
    ]
    revoked = parsed.revoked_identity_ids
    # A Slack app has one bot per workspace, so a surface that never recorded
    # its bot's user id is that bot all the same: skipping it would leave the
    # one surface nobody can tell apart reading as connected.
    account_ids = {
        surface.account_id
        for surface in surfaces
        if surface.account_id is not None
        and (
            revoked is None
            or surface.surface_identity_id is None
            or surface.surface_identity_id in revoked
        )
    }
    changed = await retire_accounts(
        uow.session, connector_id=binding.connector_id, account_ids=account_ids
    )
    await uow.commit()
    logger.info(
        "agent_surfaces.app_uninstalled.retired",
        platform=parsed.platform.value,
        tenant_id=parsed.tenant_id,
        surfaces=[str(surface.id) for surface in surfaces],
        accounts_changed=changed,
    )
    return changed
