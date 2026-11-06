"""Declaring a pod connector, and whether a caller could run one."""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.modules.connectors.domain.pod_connector import (
    PodAccountEntity,
    PodConnectorEntity,
    PodConnectorMode,
    PodConnectorStatus,
)
from app.modules.connectors.domain.auth_config import AuthConfigEntity
from app.modules.connectors.domain.pod_connector_errors import (
    PodConnectorConflictError,
)
from app.modules.connectors.infrastructure.repositories.pod_connector_repository import (
    PodConnectorRepository,
)
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
    one a call that then failed."""
    if status is not PodConnectorStatus.READY:
        return False
    if not await ctx.can(
        Permissions.CONNECTOR_USE,
        ResourceRef.connector(connector.pod_id, connector.id),
    ):
        return False
    if connector.mode is PodConnectorMode.PER_MEMBER:
        return acting_person(ctx) is not None
    return shared is not None and await may_use_shared_account(ctx, shared)


async def slug_to_declare(
    pod_connectors: PodConnectorRepository,
    pod_id: UUID,
    auth_config: AuthConfigEntity,
    *,
    requested: str | None,
) -> str:
    """The slug asked for, refused when taken; else the installation's name,
    made free in this pod -- so declaring one needs no name invented."""
    if requested is None:
        taken = {one.slug for one in await pod_connectors.list_by_pod(pod_id)}
        return free_slug(auth_config, taken)
    if await pod_connectors.get_by_slug(pod_id, requested) is not None:
        raise PodConnectorConflictError(requested)
    return requested


__all__ = ["can_use", "slug_to_declare"]
