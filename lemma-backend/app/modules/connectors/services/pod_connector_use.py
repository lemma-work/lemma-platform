"""Declaring a pod connector, and whether a caller could run one."""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.modules.connectors.api.schemas.pod_connector_schemas import (
    PodConnectorCreateRequest,
)
from app.modules.connectors.domain.pod_connector import (
    PodAccountEntity,
    PodConnectorEntity,
    PodConnectorMode,
    PodConnectorStatus,
    PodConnectorVisibility,
)
from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.domain.pod_connector_errors import (
    PodConnectorConflictError,
    PodConnectorValidationError,
)
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodConnectorRepository,
)
from app.modules.connectors.services.auth.github_bindings import GITHUB, is_github
from app.modules.connectors.services.pod_connector_resolution import (
    acting_person,
    free_slug,
    may_use_shared_account,
)


async def can_use(
    ctx: Context,
    connector: PodConnectorEntity,
    status: PodConnectorStatus,
    shared: PodAccountEntity | None,
) -> bool:
    """Whether a call by ``ctx`` would run, once its own account is in
    place: the grant alone said yes for a disabled installation, a shared
    connector with no account or one restricted away from the caller, and
    a per-member connector asked by a run with nobody behind it -- every
    one a call that then failed. An APP connector has no account half:
    ``connector.use`` and an installation that counts are the whole of it."""
    if status is not PodConnectorStatus.READY:
        return False
    if not await ctx.can(
        Permissions.CONNECTOR_USE,
        ResourceRef.connector(connector.pod_id, connector.id),
    ):
        return False
    if connector.mode is PodConnectorMode.APP:
        return True
    if connector.mode is PodConnectorMode.PER_MEMBER:
        return acting_person(ctx) is not None
    return shared is not None and await may_use_shared_account(ctx, shared)


def require_valid_mode(
    mode: PodConnectorMode,
    auth_config: AuthConfigEntity,
    *,
    shared_pod_account_id: UUID | None,
    visibility: PodConnectorVisibility,
) -> None:
    """What an APP connector may be, refused before anything is written.

    GitHub only: it is the one connector whose app Lemma can act as on an
    organization's behalf. Never with a shared account -- it runs as nobody's
    -- and only visible to the whole pod for now, the default every connector
    has: ``connector.use`` grants decide who may run it. Asked on declaring
    and on changing a connector's mode alike.
    """
    if mode is not PodConnectorMode.APP:
        return
    if not is_github(auth_config.connector_id):
        raise PodConnectorValidationError(
            "Only a GitHub connector can act as the organization's app.",
            reason="app_mode_github_only",
        )
    if shared_pod_account_id is not None:
        raise PodConnectorValidationError(
            "A connector that acts as the organization's app runs as nobody's "
            "account; leave shared_pod_account_id out.",
            reason="app_mode_no_shared_account",
        )
    if visibility is not PodConnectorVisibility.POD:
        raise PodConnectorValidationError(
            "A connector that acts as the organization's app is visible to the "
            "whole pod; who may run it is decided by connector.use grants.",
            reason="app_mode_pod_visibility_only",
        )


#: The slug an APP connector gets when the request names none and ``github``
#: is taken -- by the pod's own per-member GitHub, usually.
_APP_SLUG_FALLBACK = f"{GITHUB}-bot"


async def slug_to_declare(
    pod_connectors: PodConnectorRepository,
    pod_id: UUID,
    auth_config: AuthConfigEntity,
    request: PodConnectorCreateRequest,
) -> str:
    """The slug asked for, refused when taken; else the installation's name,
    made free in this pod -- so declaring one needs no name invented. An APP
    connector defaults to ``github`` when free, else ``github-bot``.

    The request's mode is checked first (``require_valid_mode``)."""
    require_valid_mode(
        request.mode,
        auth_config,
        shared_pod_account_id=request.shared_pod_account_id,
        visibility=request.visibility,
    )
    requested = request.slug
    if requested is None:
        taken = {one.slug for one in await pod_connectors.list_by_pod(pod_id)}
        if request.mode is PodConnectorMode.APP:
            if GITHUB not in taken:
                return GITHUB
            slug, n = _APP_SLUG_FALLBACK, 2
            while slug in taken:
                slug, n = f"{_APP_SLUG_FALLBACK}-{n}", n + 1
            return slug
        return free_slug(auth_config, taken)
    if await pod_connectors.get_by_slug(pod_id, requested) is not None:
        raise PodConnectorConflictError(requested)
    return requested


__all__ = ["can_use", "require_valid_mode", "slug_to_declare"]
