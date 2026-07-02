from __future__ import annotations

from typing import Optional, Sequence, Tuple
from uuid import UUID

from app.core.authorization.context import ActorType, Context, ResourceVisibility
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreTableEntity,
    DatastoreTableSummaryEntity,
    materialize_table_columns,
)
from app.modules.datastore.domain.errors import (
    DatastoreConflictError,
    DatastoreDomainError,
    DatastoreInfrastructureError,
    DatastoreTableNotFoundError,
)
from app.modules.datastore.domain.ports import (
    DatastoreSchemaPort,
    DatastoreTableRepositoryPort,
)
from app.modules.datastore.services.authorization import DatastoreAuthorization


class TableService:
    def __init__(
        self,
        table_repository: DatastoreTableRepositoryPort,
        schema_manager: DatastoreSchemaPort,
        authorization_service: object,
    ):
        self.table_repository = table_repository
        self.schema_manager = schema_manager
        self.authorization_service = authorization_service
        self.authz = DatastoreAuthorization(authorization_service)

    async def create_table(
        self,
        pod_id: UUID,
        table_name: str,
        primary_key_column: str,
        columns: list[ColumnSchema],
        config: dict | None,
        enable_rls: bool,
        visibility: str | None = None,
        *,
        ctx: Context,
    ) -> DatastoreTableEntity:
        entity_data: dict = {
            "pod_id": pod_id,
            "table_name": table_name,
            "primary_key_column": primary_key_column,
            "columns": columns,
            "config": config,
            "enable_rls": enable_rls,
        }
        if visibility is not None:
            entity_data["visibility"] = visibility
        entity = DatastoreTableEntity(**entity_data)

        requester_user_id = ctx.user_id
        await self.authz.require_table_create(
            user_id=requester_user_id,
            pod_id=entity.pod_id,
            ctx=ctx,
        )

        entity.user_id = requester_user_id
        self._normalize_table_visibility(entity)
        entity.validate_structure()
        entity.columns = materialize_table_columns(
            entity.primary_key_column,
            entity.columns,
            enable_rls=entity.enable_rls,
        )
        entity.validate_structure()

        existing = await self.table_repository.get_by_datastore_and_name(
            entity.pod_id,
            entity.table_name,
        )
        if existing:
            raise DatastoreConflictError(
                f"Table '{entity.table_name}' already exists in this datastore"
            )

        entity.mark_created(requester_user_id)
        table = await self.table_repository.create(entity)

        try:
            await self.schema_manager.create_table(
                entity.pod_id,
                entity.table_name,
                entity.primary_key_column,
                entity.columns,
                entity.enable_rls,
            )
        except DatastoreDomainError:
            raise
        except Exception as exc:
            raise DatastoreInfrastructureError(
                f"Failed to create table '{table_name}'"
            ) from exc

        if ctx is not None:
            refreshed = await self.table_repository.get_by_datastore_and_name(
                entity.pod_id,
                entity.table_name,
                ctx=ctx,
            )
            return refreshed or table
        return table

    async def update_table(
        self,
        pod_id: UUID,
        table_name: str,
        config: dict | None,
        ctx: Context,
        visibility: str | None = None,
        enable_rls: bool | None = None,
    ) -> DatastoreTableEntity:
        requester_user_id = ctx.user_id
        table = await self.table_repository.get_by_datastore_and_name(
            pod_id,
            table_name,
            ctx=ctx,
        )
        if not table:
            raise DatastoreTableNotFoundError(f"Table '{table_name}' not found")

        await self.authz.require_table_update(
            user_id=requester_user_id,
            pod_id=pod_id,
            table_id=table.id,
            table_name=table.table_name,
            ctx=ctx,
        )

        if config is not None:
            table.update_config(config, actor_id=requester_user_id)
        if visibility is not None:
            table.visibility = self._normalize_visibility_value(visibility).value
        if enable_rls is not None and enable_rls != table.enable_rls:
            try:
                await self.schema_manager.set_table_rls(
                    pod_id,
                    table.table_name,
                    enable_rls,
                )
            except DatastoreDomainError:
                raise
            except Exception as exc:
                raise DatastoreInfrastructureError(
                    "Failed to toggle row-level security"
                ) from exc
            table.enable_rls = enable_rls
            # Re-derive system columns so the stored schema matches the physical
            # table (user_id appears only while RLS is on).
            table.columns = materialize_table_columns(
                table.primary_key_column,
                [column for column in table.columns if not column.system],
                enable_rls=enable_rls,
            )
        updated = await self.table_repository.update(table)
        if ctx is not None:
            refreshed = await self.table_repository.get_by_datastore_and_name(
                pod_id,
                table_name,
                ctx=ctx,
            )
            return refreshed or updated
        return updated

    async def get_table(
        self,
        pod_id: UUID,
        table_name: str,
        ctx: Context,
    ) -> DatastoreTableEntity:
        requester_user_id = ctx.user_id
        table = await self.table_repository.get_by_datastore_and_name(
            pod_id,
            table_name,
            ctx=ctx,
        )
        if not table:
            raise DatastoreTableNotFoundError(f"Table '{table_name}' not found")

        await self.authz.require_table_read(
            user_id=requester_user_id,
            pod_id=pod_id,
            table_id=table.id,
            table_name=table.table_name,
            ctx=ctx,
        )

        return table

    async def list_tables(
        self,
        pod_id: UUID,
        ctx: Context,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence[DatastoreTableEntity], Optional[str]]:
        requester_user_id = ctx.user_id
        if ctx is None:
            await self.authz.require_datastore_read(
                user_id=requester_user_id,
                pod_id=pod_id,
            )
            return await self.table_repository.list_by_datastore(
                pod_id,
                limit,
                cursor,
            )
        return await self.table_repository.list_visible_by_datastore(
            pod_id,
            ctx,
            limit,
            cursor,
        )

    async def list_table_summaries(
        self,
        pod_id: UUID,
        ctx: Context,
        limit: int = 100,
        cursor: Optional[str] = None,
    ) -> Tuple[Sequence["DatastoreTableSummaryEntity"], Optional[str]]:
        return await self.table_repository.list_summaries_visible_by_datastore(
            pod_id,
            ctx,
            limit,
            cursor,
        )

    async def delete_table(
        self,
        pod_id: UUID,
        table_name: str,
        ctx: Context,
    ) -> bool:
        requester_user_id = ctx.user_id
        table = await self.table_repository.get_by_datastore_and_name(
            pod_id,
            table_name,
        )
        if not table:
            raise DatastoreTableNotFoundError("Table not found")

        # A human deleting a table they created is fine via the owner shortcut.
        # A delegated workload must ALWAYS route through authz, even for a table
        # its delegating user created — table.delete is destructive and gated
        # (needs an explicit workload grant or a session approval), so the owner
        # shortcut must not let a workload bypass that.
        is_delegated = ctx is not None and (
            ctx.actor_type == ActorType.DELEGATED_USER_WORKLOAD
        )
        if ctx is not None:
            if is_delegated or table.user_id != requester_user_id:
                await self.authz.require_table_delete(
                    user_id=requester_user_id,
                    pod_id=pod_id,
                    table_id=table.id,
                    table_name=table.table_name,
                    ctx=ctx,
                )
        elif table.user_id != requester_user_id:
            await self.authz.require_table_delete(
                user_id=requester_user_id,
                pod_id=pod_id,
                table_id=table.id,
                table_name=table.table_name,
            )

        try:
            await self.schema_manager.drop_table(pod_id, table_name)
        except DatastoreDomainError:
            raise
        except Exception as exc:
            raise DatastoreInfrastructureError(
                f"Failed to drop table '{table_name}'"
            ) from exc

        table.mark_deleted(requester_user_id)
        deleted = await self.table_repository.delete_entity(table)
        if not deleted:
            raise DatastoreTableNotFoundError("Table not found")
        return True

    async def add_column(
        self,
        pod_id: UUID,
        table_name: str,
        column: ColumnSchema,
        ctx: Context,
    ) -> DatastoreTableEntity:
        requester_user_id = ctx.user_id
        table = await self.table_repository.get_by_datastore_and_name(
            pod_id,
            table_name,
        )
        if not table:
            raise DatastoreTableNotFoundError("Table not found")

        await self.authz.require_table_update(
            user_id=requester_user_id,
            pod_id=pod_id,
            table_id=table.id,
            table_name=table.table_name,
            ctx=ctx,
        )

        table.add_column(column, actor_id=requester_user_id)

        try:
            await self.schema_manager.add_column(
                pod_id,
                table_name,
                column,
                known_columns={existing.name for existing in table.columns},
            )
        except DatastoreDomainError:
            raise
        except Exception as exc:
            raise DatastoreInfrastructureError(
                f"Failed to add column '{column.name}' to table '{table_name}'"
            ) from exc
        return await self.table_repository.update(table)

    async def remove_column(
        self,
        pod_id: UUID,
        table_name: str,
        column_name: str,
        ctx: Context,
    ) -> DatastoreTableEntity:
        requester_user_id = ctx.user_id
        table = await self.table_repository.get_by_datastore_and_name(
            pod_id,
            table_name,
        )
        if not table:
            raise DatastoreTableNotFoundError("Table not found")

        await self.authz.require_table_delete(
            user_id=requester_user_id,
            pod_id=pod_id,
            table_id=table.id,
            table_name=table.table_name,
            ctx=ctx,
        )

        table.remove_column(column_name, actor_id=requester_user_id)

        try:
            await self.schema_manager.drop_column(pod_id, table_name, column_name)
        except DatastoreDomainError:
            raise
        except Exception as exc:
            raise DatastoreInfrastructureError(
                f"Failed to remove column '{column_name}' from table '{table_name}'"
            ) from exc
        return await self.table_repository.update(table)

    def _normalize_table_visibility(self, entity: DatastoreTableEntity) -> None:
        entity.visibility = self._normalize_visibility_value(entity.visibility).value

    @staticmethod
    def _normalize_visibility_value(value: str | None) -> ResourceVisibility:
        normalized = str(value or ResourceVisibility.POD.value).strip().upper()
        if normalized in {"PERSONAL", "PRIVATE", "OWNER"}:
            return ResourceVisibility.PERSONAL
        if normalized == "RESTRICTED":
            return ResourceVisibility.RESTRICTED
        if normalized == "PUBLIC":
            return ResourceVisibility.PUBLIC
        return ResourceVisibility.POD
