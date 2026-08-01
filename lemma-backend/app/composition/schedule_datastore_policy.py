"""Datastore-backed authorization adapter for schedule application services."""

from __future__ import annotations

from uuid import UUID

from app.core.authorization.context import Context, ResourceRef
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.datastore.infrastructure.repositories import DatastoreTableRepository
from app.modules.schedule.domain.errors import ScheduleValidationError


class SqlAlchemyDatastoreSchedulePolicy:
    def __init__(self, uow: SqlAlchemyUnitOfWork) -> None:
        self.table_repository = DatastoreTableRepository(uow)

    async def require_table_update(
        self, *, pod_id: UUID, table_name: str, ctx: Context
    ) -> None:
        table = await self.table_repository.get_by_datastore_and_name(
            pod_id, table_name, ctx=ctx
        )
        if table is None:
            raise ScheduleValidationError(
                f"Datastore table '{table_name}' was not found in this pod."
            )
        await ctx.require(
            Permissions.DATASTORE_TABLE_UPDATE,
            ResourceRef.table(pod_id, table.id),
        )

    async def can_view_all_runs(
        self, *, pod_id: UUID, table_name: str, ctx: Context
    ) -> bool:
        table = await self.table_repository.get_by_datastore_and_name(
            pod_id, table_name, ctx=ctx
        )
        if table is None:
            return False
        if not table.enable_rls:
            return True
        return await ctx.can(
            Permissions.DATASTORE_TABLE_DELETE,
            ResourceRef.table(pod_id, table.id),
        )
