"""Stand down what ran on an app when the platform takes the app away.

Slack sends ``app_uninstalled`` (or ``tokens_revoked`` for the bot) and then
nothing: every later send is refused, and until this ran the surface still read
"Connected and working". Two things follow, both through paths Lemma already
has:

* the connector **account** the bot's token was on is marked
  ``REAUTH_REQUIRED`` -- what the rest of Lemma already understands. The
  surface's setup turns into "Reinstall it in Slack", and reconnecting repairs
  the account in place, so the surface keeps its channels and settings. This
  is the connectors module's ``accounts`` (a credential), not channels'
  ``external_accounts`` (who owns a place), which is left as it is;
* every group the bot was in is archived, as the bot taken out of one group
  archives it (``SurfaceGroupRepository.bot_removed``). Slack sends no leave
  per channel for an uninstall. Invited back after a reinstall, each group
  returns the way any does (``BOT_RETURNED``). A person's private chat is left
  open: it is theirs, and other places feed it.

Scoped by the receivers the signature check resolved, never by the workspace:
one Slack workspace holds Lemma's app and any number of an org's own under the
same team id, and only the app that was removed signed this event. Receivers
are one app's surfaces when the signature is checked; when it is not (local
development), they are every app's in the workspace, so they are narrowed again
to the app the event names.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.services.surface_connector import bound_account_ids
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedSurfaceLifecycleEvent,
    SurfaceLifecycleKind,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfaceIngressRequest,
)
from app.modules.agent_surfaces.domain.ports import SurfaceInstallationRepositoryPort
from app.modules.agent_surfaces.domain.surface_connectors import (
    SURFACE_CONNECTOR_BINDINGS,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.connectors.contracts.retirement import retire_accounts

logger = get_logger(__name__)


async def retire_uninstalled_app(
    uow: SqlAlchemyUnitOfWork,
    *,
    surfaces_repo: SurfaceInstallationRepositoryPort,
    credential_resolver: SurfaceCredentialResolver,
    request: SurfaceIngressRequest,
    parsed: ParsedSurfaceLifecycleEvent,
) -> int:
    """Retire the accounts the departed app's bot ran on, and archive its
    groups. Returns how many accounts changed. The caller commits."""
    if (
        parsed.kind is not SurfaceLifecycleKind.UNINSTALLED
        or parsed.platform is not SurfacePlatform.SLACK
    ):
        return 0
    binding = SURFACE_CONNECTOR_BINDINGS[parsed.platform]
    receiver_ids = _receiver_ids(request)
    # No verified receivers means no proof which app this is: a fan-in would
    # stand down every app in the workspace for one app's departure.
    if not receiver_ids or not parsed.tenant_id:
        return _unscoped(parsed, reason="no_receivers")
    # Every status, not only live: a surface paused or waiting on setup when
    # its app was removed is one somebody will bring back -- to an app that is
    # gone, still reading as connected.
    receivers = await surfaces_repo.list_active_for_routing(
        parsed.platform.value,
        surface_ids=receiver_ids,
        external_workspace_id=parsed.tenant_id,
        also_paused=True,
    )
    surfaces = await _of_the_named_app(
        receivers,
        credential_resolver=credential_resolver,
        api_app_id=str(parsed.raw_payload.get("api_app_id") or "").strip() or None,
    )
    if surfaces is None:
        return _unscoped(parsed, reason="several_apps")
    retired = _of_the_revoked_bots(surfaces, parsed.revoked_identity_ids)
    changed = await retire_accounts(
        uow.session,
        connector_id=binding.connector_id,
        account_ids=set((await bound_account_ids(uow, retired)).values()),
    )
    archived = await SurfaceGroupRepository(uow.session).bots_removed_everywhere(
        [surface.id for surface in retired]
    )
    logger.info(
        "agent_surfaces.app_uninstalled.retired",
        platform=parsed.platform.value,
        tenant_id=parsed.tenant_id,
        # A string: below error level a list field is dropped from the record.
        surface_ids=",".join(str(surface.id) for surface in retired),
        accounts_changed=changed,
        groups_archived=archived,
    )
    return changed


def _receiver_ids(request: SurfaceIngressRequest) -> list[UUID]:
    if isinstance(request, SurfaceDirectWebhookIngress):
        return [request.surface_id]
    return list(request.receiver_surface_ids or [])


def _of_the_revoked_bots(
    surfaces: Sequence[AgentSurfaceEntity], revoked: Sequence[str] | None
) -> list[AgentSurfaceEntity]:
    """Every surface for an uninstall; for a revocation, the named bots'.

    The surfaces are one app's, and a Slack app has one bot per workspace, so
    a surface that never recorded its bot's user id is that bot all the same:
    skipping it would leave the one surface nobody can tell apart reading as
    connected.
    """
    if revoked is None:
        return list(surfaces)
    return [
        surface
        for surface in surfaces
        if surface.surface_identity_id is None or surface.surface_identity_id in revoked
    ]


async def _of_the_named_app(
    receivers: Sequence[AgentSurfaceEntity],
    *,
    credential_resolver: SurfaceCredentialResolver,
    api_app_id: str | None,
) -> list[AgentSurfaceEntity] | None:
    """The receivers that are the app the event is about; None when that
    cannot be told.

    Slack names the app (``api_app_id``) on every event callback, so that is
    the answer. An event that names none is about one app only if its
    receivers are one app's.
    """
    app_of = {
        surface.id: (
            await credential_resolver.slack_webhook_credentials(surface)
        ).app_id
        for surface in receivers
    }
    if api_app_id is not None:
        return [surface for surface in receivers if app_of[surface.id] == api_app_id]
    return list(receivers) if len(set(app_of.values())) <= 1 else None


def _unscoped(parsed: ParsedSurfaceLifecycleEvent, *, reason: str) -> int:
    logger.info(
        "agent_surfaces.app_uninstalled.unscoped",
        platform=parsed.platform.value,
        tenant_id=parsed.tenant_id,
        reason=reason,
    )
    return 0
