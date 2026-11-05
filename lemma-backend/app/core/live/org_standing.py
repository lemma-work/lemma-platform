"""Readers' standing in an organization's pods, as REST decides it.

The ``org`` hub asks, per turn, whether a reader may open a pod (``pod.read``)
and whether they are in an item's audience there. Each reader's role snapshot
in each pod is built once per turn -- a cached read -- and each permission is
asked of it once.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.authorization.service import AuthorizationDataService
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.live.org_feeds import Audience
from app.core.live.org_readers import Standing


#: Opens a ``Standing`` for one turn, released when it ends.
type StandingFactory = Callable[[], AbstractAsyncContextManager[Standing]]


def database_standing(
    uow_factory: UnitOfWorkFactory, organization_id: UUID
) -> StandingFactory:
    """Standing as REST decides it: each reader's role snapshot in each pod."""

    @asynccontextmanager
    async def opened() -> AsyncIterator[Standing]:
        async with uow_factory() as uow:
            yield _Access(uow, organization_id)

    return opened


class _Access:
    """Readers' standing in pods, worked out once per turn: a role snapshot
    per (reader, pod), each permission asked of it once."""

    def __init__(self, uow: SqlAlchemyUnitOfWork, organization_id: UUID) -> None:
        self._service = AuthorizationDataService(uow.session)
        self._organization_id = organization_id
        self._contexts: dict[tuple[UUID, UUID], Context | None] = {}
        self._answers: dict[tuple[UUID, UUID, str, ResourceRef | None], bool] = {}

    async def _context(self, user_id: UUID, pod_id: UUID) -> Context | None:
        key = (user_id, pod_id)
        if key not in self._contexts:
            try:
                ctx = await self._service.build_user_context(
                    user_id=user_id, pod_id=pod_id
                )
            except DomainError:
                ctx = None
            # A pod whose snapshot names another organization is not this
            # organization's to tell of, whatever a listing said.
            if ctx is not None and ctx.organization_id not in (
                None,
                self._organization_id,
            ):
                ctx = None
            self._contexts[key] = ctx
        return self._contexts[key]

    async def can(
        self,
        user_id: UUID,
        pod_id: UUID,
        permission: str,
        resource: ResourceRef | None = None,
    ) -> bool:
        key = (user_id, pod_id, permission, resource)
        if key not in self._answers:
            ctx = await self._context(user_id, pod_id)
            self._answers[key] = ctx is not None and await ctx.can(
                permission, resource or ResourceRef.pod(pod_id, self._organization_id)
            )
        return self._answers[key]

    async def opens(self, user_id: UUID, pod_id: UUID) -> bool:
        return await self.can(user_id, pod_id, Permissions.POD_READ)

    async def admits(self, user_id: UUID, pod_id: UUID, audience: Audience) -> bool:
        if audience.users is not None and user_id not in audience.users:
            return False
        if audience.permission is None:
            return True
        return await self.can(user_id, pod_id, audience.permission, audience.resource)


__all__ = ["StandingFactory", "database_standing"]
