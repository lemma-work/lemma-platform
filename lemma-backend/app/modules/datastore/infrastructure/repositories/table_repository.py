from __future__ import annotations

from typing import Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy import select

from app.core.authorization.context import Context, ResourceType, ResourceVisibility
from app.core.authorization.grants import delete_resource_sharing_grants
from app.core.authorization.permissions import Permissions
from app.core.authorization.sql_actions import (
    allowed_actions_contains,
    allowed_actions_expr,
)
from app.modules.datastore.domain.datastore_entities import (
    RESERVED_TABLE_PREFIX,
    DatastoreTableEntity,
    DatastoreTableSummaryEntity,
)
from app.modules.datastore.domain.errors import DatastoreTableNotFoundError
from app.modules.datastore.domain.ports import DatastoreTableRepositoryPort
from app.modules.datastore.infrastructure.models import DatastoreTable
from app.modules.datastore.infrastructure.repositories._base import (
    DatastoreRepositoryBase,
)


def _table_actions_expr(ctx: Context):
    return allowed_actions_expr(
        ctx=ctx,
        resource_type=ResourceType.DATASTORE_TABLE,
        resource_id_col=DatastoreTable.id,
        pod_id_col=DatastoreTable.pod_id,
        owner_user_id_col=DatastoreTable.user_id,
        visibility_col=DatastoreTable.visibility,
    )


def _not_reserved():
    """Exclude system-managed ``reserved_*`` tables from user-facing listings.

    ``autoescape`` treats the ``_`` in the prefix as a literal, not a LIKE
    wildcard, so only real ``reserved_``-prefixed names are filtered.
    """
    return ~DatastoreTable.table_name.startswith(
        RESERVED_TABLE_PREFIX, autoescape=True
    )


class DatastoreTableRepository(DatastoreRepositoryBase, DatastoreTableRepositoryPort):
    """Persistence for table metadata (the application DB)."""

    async def create(self, entity: DatastoreTableEntity) -> DatastoreTableEntity:
        instance = DatastoreTable(**entity.model_dump(exclude={"allowed_actions"}))
        self.session.add(instance)
        await self.session.flush()
        self._collect_events(entity)
        return instance.to_entity()

    async def get(self, id: UUID) -> Optional[DatastoreTableEntity]:
        result = await self.session.execute(
            select(DatastoreTable).where(DatastoreTable.id == id)
        )
        instance = result.scalars().first()
        return instance.to_entity() if instance else None

    async def update(self, entity: DatastoreTableEntity) -> DatastoreTableEntity:
        result = await self.session.execute(
            select(DatastoreTable).where(DatastoreTable.id == entity.id)
        )
        instance = result.scalars().first()
        if not instance:
            raise DatastoreTableNotFoundError()

        if (
            instance.visibility == ResourceVisibility.RESTRICTED.value
            and entity.visibility != ResourceVisibility.RESTRICTED.value
        ):
            await delete_resource_sharing_grants(
                self.session,
                pod_id=entity.pod_id,
                resource_type=ResourceType.DATASTORE_TABLE,
                resource_id=entity.id,
            )

        data = entity.model_dump(exclude_unset=True)
        for key, value in data.items():
            if key in {"id", "created_at", "updated_at"}:
                continue
            if hasattr(instance, key):
                setattr(instance, key, value)

        await self.session.flush()
        self._collect_events(entity)
        return instance.to_entity()

    async def delete_entity(self, entity: DatastoreTableEntity) -> bool:
        result = await self.session.execute(
            select(DatastoreTable).where(DatastoreTable.id == entity.id)
        )
        instance = result.scalars().first()
        if not instance:
            return False
        self._collect_events(entity)
        await self.session.delete(instance)
        return True

    async def get_by_datastore_and_name(
        self,
        pod_id: UUID,
        table_name: str,
        ctx: Context | None = None,
    ) -> Optional[DatastoreTableEntity]:
        if ctx is None:
            result = await self.session.execute(
                select(DatastoreTable).where(
                    DatastoreTable.pod_id == pod_id,
                    DatastoreTable.table_name == table_name,
                )
            )
            table = result.scalars().first()
            return table.to_entity() if table else None

        actions = _table_actions_expr(ctx)
        result = await self.session.execute(
            select(DatastoreTable, actions).where(
                DatastoreTable.pod_id == pod_id,
                DatastoreTable.table_name == table_name,
            )
        )
        row = result.first()
        return self._with_allowed_actions(row[0].to_entity(), row[1]) if row else None

    async def list_by_datastore(
        self, pod_id: UUID, limit: int = 100, cursor: Optional[str] = None
    ) -> Tuple[Sequence[DatastoreTableEntity], Optional[str]]:
        stmt = select(DatastoreTable).where(
            DatastoreTable.pod_id == pod_id,
            _not_reserved(),
        )
        if cursor:
            stmt = stmt.where(DatastoreTable.id > UUID(cursor))
        stmt = stmt.order_by(DatastoreTable.id).limit(limit + 1)
        result = await self.session.execute(stmt)
        tables = list(result.scalars().all())

        next_cursor = None
        if len(tables) > limit:
            next_cursor = str(tables[limit - 1].id)
            tables = tables[:limit]
        return [table.to_entity() for table in tables], next_cursor

    @staticmethod
    def _to_summary(
        model: DatastoreTable, allowed: list[str] | tuple[str, ...]
    ) -> DatastoreTableSummaryEntity:
        # Derive column_count from raw JSONB without validating every column into
        # ColumnSchema (that per-column validation is the bulk of the list cost).
        return DatastoreTableSummaryEntity(
            id=model.id,
            pod_id=model.pod_id,
            table_name=model.table_name,
            primary_key_column=model.primary_key_column,
            column_count=len(model.columns or []),
            enable_rls=model.enable_rls,
            visibility=model.visibility,
            allowed_actions=list(allowed),
            created_at=model.created_at,
            updated_at=model.updated_at,
        )

    async def list_summaries_visible_by_datastore(
        self,
        pod_id: UUID,
        ctx: Context,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[DatastoreTableSummaryEntity], Optional[str]]:
        actions = _table_actions_expr(ctx)
        stmt = select(DatastoreTable, actions).where(
            DatastoreTable.pod_id == pod_id,
            _not_reserved(),
            allowed_actions_contains(actions, Permissions.DATASTORE_TABLE_READ),
        )
        if cursor:
            stmt = stmt.where(DatastoreTable.id > UUID(cursor))
        stmt = stmt.order_by(DatastoreTable.id).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.all())

        next_cursor = None
        if len(rows) > limit:
            next_cursor = str(rows[limit - 1][0].id)
            rows = rows[:limit]
        return [self._to_summary(table, allowed) for table, allowed in rows], next_cursor

    async def list_visible_by_datastore(
        self,
        pod_id: UUID,
        ctx: Context,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[DatastoreTableEntity], Optional[str]]:
        actions = _table_actions_expr(ctx)
        stmt = select(DatastoreTable, actions).where(
            DatastoreTable.pod_id == pod_id,
            _not_reserved(),
            allowed_actions_contains(actions, Permissions.DATASTORE_TABLE_READ),
        )
        if cursor:
            stmt = stmt.where(DatastoreTable.id > UUID(cursor))
        stmt = stmt.order_by(DatastoreTable.id).limit(limit + 1)
        result = await self.session.execute(stmt)
        rows = list(result.all())

        next_cursor = None
        if len(rows) > limit:
            next_cursor = str(rows[limit - 1][0].id)
            rows = rows[:limit]
        return [
            self._with_allowed_actions(table.to_entity(), allowed)
            for table, allowed in rows
        ], next_cursor
