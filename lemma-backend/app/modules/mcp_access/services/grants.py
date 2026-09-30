"""The person's side of their grants: seeing them, and ending them."""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.mcp_access.domain.entities import ConnectedApp
from app.modules.mcp_access.infrastructure.repositories import McpAccessRepository

MAX_LISTED = 200
"""A person connects a handful of clients; this is a bound, not a page size."""


class GrantService:
    def __init__(
        self,
        uow_factory: UnitOfWorkFactory,
        *,
        clock: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        self._uow_factory = uow_factory
        self._now = clock

    async def list(self, *, user_id: UUID, pod_id: UUID | None) -> list[ConnectedApp]:
        async with self._uow_factory() as uow:
            return await McpAccessRepository(uow).list_connected_apps(
                user_id=user_id, pod_id=pod_id, limit=MAX_LISTED
            )

    async def revoke(self, *, user_id: UUID, grant_id: UUID) -> bool:
        """False when there is no such live grant *of this person's* -- one
        answer for "never existed", "already revoked" and "somebody else's"."""
        async with self._uow_factory() as uow:
            revoked = await McpAccessRepository(uow).revoke_grant(
                grant_id=grant_id, now=self._now(), user_id=user_id
            )
            await uow.commit()
        return revoked
