from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Optional, Sequence, Tuple
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.log.log import get_logger
from app.core.authorization.context import (
    ActorType,
    Context,
    ResourceVisibility,
    normalize_resource_visibility,
)
from app.modules.datastore.domain.datastore_entities import (
    ColumnSchema,
    DatastoreTableEntity,
    DatastoreTableSummaryEntity,
    ensure_table_name_available,
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
from app.modules.datastore.services.table_context import TableHydration

logger = get_logger(__name__)


class TableService:
    """Schema changes across two databases, in one deliberate order.

    A table is a metadata row in the application database and a physical table
    in the pod's datastore database. No transaction spans both, so every schema
    change has a window in which one has landed and the other has not, and the
    only thing that decides what an interrupted request leaves behind is which
    one is committed first.

    The rule: **a physical object is created after the row that describes it,
    and dropped before that row is removed.** The metadata may over-report,
    never under-report, because only one of the two residues is recoverable
    through the API:

    * a metadata row with no physical table -- ``table.list`` shows it, reads
      fail, and re-issuing ``table.delete`` clears it (``DROP TABLE IF EXISTS``
      makes that idempotent). The user can get out of it.
    * a physical table with no metadata row -- invisible to ``table.list``,
      404 from ``table.get`` and ``table.delete``, and ``table.create`` fails
      the DDL with "already exists" and rolls its own metadata insert back.
      Nothing the user can do frees the name.

    Committing the metadata first means an ordinary DDL *failure* would leave
    the first residue for no reason, so each direction compensates: the window
    is for the crash, not for the refusal. Cleaning up what a crash does leave
    wants a reconciliation sweep (drop physical tables with no metadata row,
    flag rows with no table); that is separate work, not done here.
    """

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
        ensure_table_name_available(table_name)
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
        # Metadata first — see the ordering rule on this class.
        await self.table_repository.commit()

        try:
            await self.schema_manager.create_table(
                entity.pod_id,
                entity.table_name,
                entity.primary_key_column,
                entity.columns,
                entity.enable_rls,
            )
        except Exception as exc:
            await self._undo_metadata(
                table,
                lambda: self.table_repository.delete_entity(table),
                change="create table",
            )
            if isinstance(exc, DatastoreDomainError):
                raise
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
            hydration=TableHydration.of(table),
        )

        return table

    async def get_tables(
        self,
        pod_id: UUID,
        table_names: Sequence[str],
        ctx: Context,
    ) -> dict[str, DatastoreTableEntity]:
        """``get_table`` for several names, in one read.

        The per-table READ check still runs per table — it is the same
        ``ctx.require`` with the same reason codes, and nothing here decides
        access from the projected actions. What is batched is the *lookup*:
        one statement instead of one per name, with each table's visibility
        and owner carried into the check so it does not read the row again.

        Raises ``DatastoreTableNotFoundError`` for the first missing name, in
        sorted order, so the error a caller sees does not depend on how the
        database happened to order the rows.
        """
        requester_user_id = ctx.user_id
        tables = await self.table_repository.get_many_by_datastore_and_names(
            pod_id, table_names, ctx=ctx
        )
        for table_name in sorted(set(table_names)):
            table = tables.get(table_name)
            if table is None:
                raise DatastoreTableNotFoundError(f"Table '{table_name}' not found")
            await self.authz.require_table_read(
                user_id=requester_user_id,
                pod_id=pod_id,
                table_id=table.id,
                table_name=table.table_name,
                ctx=ctx,
                hydration=TableHydration.of(table),
            )
        return tables

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

        # Physical table first, then the row — the ordering rule on this class
        # read in the destroying direction. Removing the row first would leave
        # a table nothing describes, which is the residue nobody can clear.
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

        declared_columns = list(table.columns)
        table.add_column(column, actor_id=requester_user_id)
        # Metadata first — see the ordering rule on this class. A physical
        # column no declared schema mentions cannot be dropped through the API
        # (``remove_column`` refuses a column the metadata does not list), so
        # the name would 409 against a column ``table.get`` never shows.
        updated = await self.table_repository.update(table)
        await self.table_repository.commit()

        try:
            await self.schema_manager.add_column(
                pod_id,
                table_name,
                column,
                known_columns={existing.name for existing in table.columns},
            )
        except Exception as exc:
            table.columns = declared_columns
            await self._undo_metadata(
                table,
                lambda: self.table_repository.update(table),
                change=f"add column '{column.name}'",
            )
            if isinstance(exc, DatastoreDomainError):
                raise
            raise DatastoreInfrastructureError(
                f"Failed to add column '{column.name}' to table '{table_name}'"
            ) from exc
        return updated

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

    async def _undo_metadata(
        self,
        table: DatastoreTableEntity,
        undo: Callable[[], Awaitable[object]],
        *,
        change: str,
    ) -> None:
        """Roll back a committed metadata change whose DDL did not land.

        The commit that precedes the DDL exists so a *crash* leaves the
        recoverable residue; an ordinary refusal — an unsupported column type,
        a name the database rejects — should leave nothing at all. If the undo
        itself fails, what remains is a table the caller can delete, which is
        the direction the ordering was chosen for. Never masks the original
        error: the caller re-raises it.
        """
        try:
            await undo()
            await self.table_repository.commit()
        except SQLAlchemyError, DatastoreDomainError:
            logger.warning(
                "datastore.table_service.metadata_undo_failed.degraded",
                pod_id=str(table.pod_id),
                table_name=table.table_name,
                change=change,
                exc_info=True,
            )

    def _normalize_table_visibility(self, entity: DatastoreTableEntity) -> None:
        entity.visibility = self._normalize_visibility_value(entity.visibility).value

    @staticmethod
    def _normalize_visibility_value(value: str | None) -> ResourceVisibility:
        return normalize_resource_visibility(value) or ResourceVisibility.POD
