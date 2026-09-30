"""Connected clients: a person's own, and -- for a pod's admins -- everyone's.

A pod admin answers for what can read the pod, so they see every member's
connections to it and can end any of them. The standing that confers this is
the one that already lets them add and remove the pod's members.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from app.core.authorization.factory import create_authorization_data_service
from app.core.authorization.permissions import Permissions
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.mcp_access.domain.entities import ConnectedApp
from app.modules.mcp_access.infrastructure.repositories import McpAccessRepository

logger = get_logger(__name__)

MAX_LISTED = 200
"""A pod's connections number in the dozens; this is a bound, not a page size."""

ADMIN_PERMISSION = Permissions.POD_MEMBER_MANAGE


class NotPodAdmin(DomainError):
    def __init__(self) -> None:
        super().__init__(
            "Only this pod's admins can see everyone's connections.",
            code="MCP_ACCESS_NOT_POD_ADMIN",
            status_code=403,
        )


class GrantService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._uow_factory = uow_factory
        self._now = clock

    async def list(
        self, *, user_id: UUID, pod_id: UUID | None, everyone: bool = False
    ) -> list[ConnectedApp]:
        """The person's own connections; with ``everyone``, every member's in
        ``pod_id``, which only the pod's admins may ask for."""
        async with self._uow_factory() as uow:
            if everyone:
                if pod_id is None or not await self._administers(uow, user_id, pod_id):
                    raise NotPodAdmin()
            return await McpAccessRepository(uow).list_connected_apps(
                user_id=None if everyone else user_id, pod_id=pod_id, limit=MAX_LISTED
            )

    async def revoke(self, *, user_id: UUID, grant_id: UUID) -> bool:
        """End one of the person's own connections, or -- as a pod admin --
        anyone's in their pod. False for one answer to "never existed",
        "already ended" and "not yours to end"."""
        now = self._now()
        async with self._uow_factory() as uow:
            repository = McpAccessRepository(uow)
            owner = await repository.grant_owner(grant_id)
            if owner is None:
                return False
            owner_id, pod_id = owner
            if owner_id != user_id and not await self._administers(
                uow, user_id, pod_id
            ):
                return False
            revoked = await repository.revoke_grant(grant_id=grant_id, now=now)
            await uow.commit()
        if revoked:
            logger.info(
                "mcp_access.grant.revoked",
                grant_id=str(grant_id),
                pod_id=str(pod_id),
                by_admin=owner_id != user_id,
            )
        return revoked

    @staticmethod
    async def _administers(
        uow: SqlAlchemyUnitOfWork, user_id: UUID, pod_id: UUID
    ) -> bool:
        ctx = await create_authorization_data_service(uow).build_user_context(
            user_id=user_id, pod_id=pod_id
        )
        return not ctx.pod_is_deleted and ctx.has_permission(ADMIN_PERMISSION)
