"""Reading a pod's connections, with the GitHub App's installations fresh.

Whether the organization's GitHub bot can be offered -- and whether an APP
connector is ready -- depends on which GitHub account each App installation is
on, which is GitHub's to say. So the read first asks GitHub about any binding
it has not been asked about for a day, and only then reads. The three steps
run in scopes of their own, so no connection is held while GitHub answers:
authorize and learn the organization, refresh the installations, read.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from fastapi import Request

from app.core.authorization.context import ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.authorization.scope import pod_context_scope
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.connectors.api.schemas.pod_connection_schemas import (
    PodConnectionsResponse,
)
from app.modules.connectors.services.auth.github_app_installations import (
    refresh_app_installations,
)
from app.modules.connectors.services.pod_connections import PodConnectionsReader

#: ``(uow_factory, organization_id) -> None``: brings the App's installations
#: up to date (``refresh_app_installations``).
InstallationsRefresh = Callable[[UnitOfWorkFactory, UUID], Awaitable[None]]


async def read_pod_connections(
    uow_factory: UnitOfWorkFactory,
    *,
    request: Request,
    user_id: UUID,
    pod_id: UUID,
    build_reader: Callable[[SqlAlchemyUnitOfWork], PodConnectionsReader],
    refresh: InstallationsRefresh = refresh_app_installations,
) -> PodConnectionsResponse:
    async with pod_context_scope(
        uow_factory, request=request, user_id=user_id, pod_id=pod_id
    ) as scope:
        # Refused before GitHub is asked anything on the caller's behalf.
        await scope.ctx.require(Permissions.POD_READ, ResourceRef.pod(pod_id))
        organization_id = scope.ctx.organization_id
    if organization_id is not None:
        await refresh(uow_factory, organization_id)
    async with pod_context_scope(
        uow_factory, request=request, user_id=user_id, pod_id=pod_id
    ) as scope:
        return await build_reader(scope.uow).read(scope.ctx, pod_id)


__all__ = ["InstallationsRefresh", "read_pod_connections"]
