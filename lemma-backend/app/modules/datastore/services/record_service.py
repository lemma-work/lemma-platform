from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.authorization.context import Context
from app.core.domain.message_bus import MessageBus
from app.core.infrastructure.events.message_bus import get_message_bus
from app.modules.datastore.domain.errors import (
    DatastoreAccessDeniedError,
    DatastoreRecordNotFoundError,
    DatastoreValidationError,
)
from app.modules.datastore.domain.datastore_entities import DatastoreDataType
from app.modules.datastore.domain.ports import DatastoreRecordRepositoryPort
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.record_validator import (
    RecordValidator,
    convert_record,
)
from app.modules.datastore.services.sql_introspection import analyze_query
from app.modules.datastore.services.table_context import TableContext

if TYPE_CHECKING:
    from app.modules.datastore.services.table_service import TableService
from app.modules.datastore.domain.events import (
    DATASTORE_EVENTS_STREAM,
    DatastoreRecordEvent,
    DatastoreRecordOperation,
)
from app.modules.identity.domain.ports import UserRepositoryPort
from app.core.log.log import get_logger

logger = get_logger(__name__)


class RecordService:
    def __init__(
        self,
        record_repository: DatastoreRecordRepositoryPort,
        message_bus: MessageBus | None = None,
        authorization_service: object | None = None,
        user_repository: UserRepositoryPort | None = None,
    ):
        self.record_repository = record_repository
        self.message_bus = message_bus or get_message_bus()
        self.authorization_service = authorization_service
        self.authz = (
            DatastoreAuthorization(authorization_service)
            if authorization_service is not None
            else None
        )
        self.user_repository = user_repository

    async def _require_datastore_read(
        self,
        *,
        user_id: UUID,
        pod_id: UUID,
        ctx: Context | None = None,
    ) -> None:
        if self.authz is None:
            return
        await self.authz.require_datastore_read(user_id=user_id, pod_id=pod_id, ctx=ctx)

    async def _require_record_read(self, *, user_id: UUID, ctx: TableContext) -> None:
        if self.authz is None:
            return
        await self.authz.require_record_read(user_id=user_id, ctx=ctx)

    async def _require_record_write(self, *, user_id: UUID, ctx: TableContext) -> None:
        if self.authz is None:
            return
        await self.authz.require_record_write(user_id=user_id, ctx=ctx)

    async def _should_enforce_user_scope(
        self,
        *,
        user_id: UUID,
        ctx: TableContext,
        admin_mode: bool = False,
    ) -> bool:
        if self.authz is None:
            # No authorization gateway wired (trusted in-process caller, e.g. the
            # pod-member sync service): fail closed to per-user scoping. Admin
            # mode needs the gateway to validate the caller, so it is ignored
            # here rather than honored unchecked.
            return ctx.enable_rls
        return await self.authz.should_enforce_record_user_scope(
            user_id=user_id,
            ctx=ctx,
            admin_mode=admin_mode,
        )

    async def _emit_record_event(
        self,
        ctx: TableContext,
        record_id: str,
        operation: DatastoreRecordOperation,
        payload: dict[str, Any],
        user_id: UUID,
        owner_user_id: UUID | None = None,
    ) -> None:
        if not ctx.events_enabled:
            return

        try:
            # Only RLS tables carry an owner; for everyone else the row is shared
            # across the pod, so subscribers must not scope it to a single user.
            # On RLS tables the owner defaults to the acting user, which is correct
            # for inserts and self-edits (RLS forces ``user_id == caller``); an
            # admin-mode write to another user's row passes the resolved owner.
            event_owner = (owner_user_id or user_id) if ctx.enable_rls else None
            event = DatastoreRecordEvent.create(
                pod_id=ctx.pod_id,
                table_name=ctx.table_name,
                record_id=str(record_id),
                operation=operation,
                payload=payload,
                actor_id=user_id,
                owner_user_id=event_owner,
            )
            await self.message_bus.publish(DATASTORE_EVENTS_STREAM, event)
        except Exception as exc:
            logger.error("Failed to emit record event: %s", exc)

    def _validate_update_payload(
        self,
        ctx: TableContext,
        data: dict[str, Any],
    ) -> None:
        errors: list[str] = []
        error_details: list[dict[str, Any]] = []

        for key, value in data.items():
            column = ctx.get_column(key)
            if column is None:
                continue
            if key == ctx.primary_key_column:
                errors.append(f"Cannot modify primary key column '{key}'")
                error_details.append({"field": key, "reason": "primary_key"})
            elif column.computed:
                errors.append(f"Cannot provide value for computed column '{key}'")
                error_details.append({"field": key, "reason": "computed"})
            elif column.system and not RecordValidator.allows_creation_override(column):
                errors.append(f"Cannot provide value for system-managed column '{key}'")
                error_details.append({"field": key, "reason": "system_managed"})
            elif (
                column.type == DatastoreDataType.ENUM
                and column.options
                and value is not None
                and value not in column.options
            ):
                allowed = ", ".join(column.options)
                errors.append(
                    f"Value '{value}' is not allowed for column '{key}'. "
                    f"Allowed values: {allowed}"
                )
                error_details.append({
                    "field": key,
                    "reason": "enum",
                    "value": value,
                    "allowed_values": column.options,
                })

        if errors:
            raise DatastoreValidationError(
                f"Invalid record data: {'; '.join(errors)}",
                details={"errors": error_details},
            )

    async def _validate_user_reference_columns(
        self,
        ctx: TableContext,
        data: dict[str, Any],
    ) -> None:
        if self.user_repository is None:
            return

        converted = convert_record(ctx.columns, data, skip_auto=False)
        checked_user_ids: set[UUID] = set()

        for key, value in converted.items():
            column = ctx.get_column(key)
            if (
                column is None
                or column.type != DatastoreDataType.USER
                or value is None
            ):
                continue

            user_id = value if isinstance(value, UUID) else UUID(str(value))
            if user_id in checked_user_ids:
                continue

            user = await self.user_repository.get(user_id)
            if user is None:
                raise DatastoreValidationError(
                    f"User does not exist for column '{key}'"
                )
            checked_user_ids.add(user_id)

    async def create_record(
        self,
        ctx: TableContext,
        data: dict[str, Any],
        user_id: UUID,
    ):
        await self._require_record_write(user_id=user_id, ctx=ctx)

        validator = RecordValidator(ctx)
        sanitized_data = validator.strip_system_write_overrides(data)

        is_valid, errors, error_details = validator.validate(sanitized_data, is_creation=True)
        if not is_valid:
            raise DatastoreValidationError(
                f"Invalid record data: {'; '.join(errors)}",
                details={"errors": error_details},
            )

        await self._validate_user_reference_columns(ctx, sanitized_data)
        record = await self.record_repository.create_record(ctx, sanitized_data, user_id)
        await self._emit_record_event(
            ctx,
            str(record.id),
            DatastoreRecordOperation.INSERT,
            sanitized_data,
            user_id,
            owner_user_id=record.user_id,
        )
        return record

    async def get_record(
        self,
        ctx: TableContext,
        record_id,
        user_id: UUID,
        *,
        admin_mode: bool = False,
    ):
        await self._require_record_read(user_id=user_id, ctx=ctx)
        return await self.record_repository.get_record(
            ctx,
            record_id,
            user_id,
            enforce_user_scope=await self._should_enforce_user_scope(
                user_id=user_id,
                ctx=ctx,
                admin_mode=admin_mode,
            ),
        )

    async def execute_readonly_query(
        self,
        *,
        pod_id: UUID,
        query: str,
        user_id: UUID,
        table_service: "TableService",
        ctx: Context,
        admin_mode: bool = False,
    ) -> tuple[list[dict], int]:
        """Validate, authorize, and run an ad-hoc read-only SQL query.

        Parses the statement (single, read-only, no cross-schema references) and
        enforces per-table ``DATASTORE_TABLE_READ`` for every referenced table via
        ``table_service.get_table``. RLS-enabled tables are row-filtered at the
        database layer.

        Rows of RLS tables are scoped to ``user_id`` by default — for every
        caller, pod admins included — so apps and functions reading through this
        endpoint see the same per-user data the record APIs do. ``admin_mode`` is
        the explicit opt-in for the full, cross-user row set; it is honored only
        when the caller administers *every* referenced RLS table (one session-wide
        flag governs all RLS tables, so admin must hold on each), otherwise the
        request is rejected with a 403 rather than silently scoped. A query that
        references no RLS table ignores ``admin_mode`` (nothing to widen).
        """
        analysis = analyze_query(query)

        saw_rls = False
        admin_on_all_rls = True
        for table_name in sorted(analysis.tables):
            # Always enforce per-table READ; only consult the admin signal when
            # admin mode is requested, so the default path does no extra work.
            table = await table_service.get_table(pod_id, table_name, ctx=ctx)
            if table.enable_rls:
                saw_rls = True
                if admin_mode and (
                    self.authz is None
                    or not await self.authz.can_admin_table(
                        pod_id=pod_id, table_id=table.id, ctx=ctx
                    )
                ):
                    admin_on_all_rls = False

        if not analysis.tables:
            # No registered table referenced (e.g. SELECT from a set-returning
            # function); fall back to a pod-level read check since there is no
            # per-table grant to authorize against.
            await self._require_datastore_read(
                user_id=user_id, pod_id=pod_id, ctx=ctx
            )

        is_pod_admin = False
        if admin_mode and saw_rls:
            if not admin_on_all_rls:
                raise DatastoreAccessDeniedError(
                    "Admin mode requires permission to administer every "
                    "RLS-enabled table referenced by the query."
                )
            is_pod_admin = True

        return await self.record_repository.execute_readonly_query(
            pod_id=pod_id,
            query=query,
            user_id=user_id,
            enable_rls=True,
            is_pod_admin=is_pod_admin,
        )

    async def list_records(
        self,
        ctx: TableContext,
        user_id: UUID,
        limit: int = 20,
        offset: int = 0,
        sorts: list[tuple[str, str]] | None = None,
        filters: list[tuple[str, str, Any]] | None = None,
        *,
        admin_mode: bool = False,
    ):
        await self._require_record_read(user_id=user_id, ctx=ctx)
        return await self.record_repository.list_records(
            ctx,
            user_id,
            limit,
            offset,
            sorts,
            filters,
            enforce_user_scope=await self._should_enforce_user_scope(
                user_id=user_id,
                ctx=ctx,
                admin_mode=admin_mode,
            ),
        )

    async def update_record(
        self,
        ctx: TableContext,
        record_id: Any,
        data: dict[str, Any],
        user_id: UUID,
        *,
        admin_mode: bool = False,
    ):
        await self._require_record_write(user_id=user_id, ctx=ctx)

        sanitized_data = RecordValidator(ctx).strip_system_write_overrides(data)
        self._validate_update_payload(ctx, sanitized_data)
        await self._validate_user_reference_columns(ctx, sanitized_data)

        record = await self.record_repository.update_record(
            ctx,
            record_id,
            sanitized_data,
            user_id,
            enforce_user_scope=await self._should_enforce_user_scope(
                user_id=user_id,
                ctx=ctx,
                admin_mode=admin_mode,
            ),
        )
        await self._emit_record_event(
            ctx,
            str(record.id),
            DatastoreRecordOperation.UPDATE,
            sanitized_data,
            user_id,
            owner_user_id=record.user_id,
        )
        return record

    async def delete_record(
        self,
        ctx: TableContext,
        record_id: Any,
        user_id: UUID,
        *,
        admin_mode: bool = False,
    ) -> bool:
        await self._require_record_write(user_id=user_id, ctx=ctx)
        deleted = await self.record_repository.delete_record(
            ctx,
            record_id,
            user_id,
            enforce_user_scope=await self._should_enforce_user_scope(
                user_id=user_id,
                ctx=ctx,
                admin_mode=admin_mode,
            ),
        )
        if deleted:
            await self._emit_record_event(
                ctx, str(record_id), DatastoreRecordOperation.DELETE, {}, user_id
            )
        return deleted

    async def bulk_create_records(
        self,
        ctx: TableContext,
        records: list[dict[str, Any]],
        user_id: UUID,
        *,
        upsert: bool = False,
    ):
        await self._require_record_write(user_id=user_id, ctx=ctx)
        if not records:
            return 0

        validator = RecordValidator(ctx)
        sanitized_records = [
            validator.strip_system_write_overrides(record) for record in records
        ]

        for record in sanitized_records:
            is_valid, errors, error_details = validator.validate(record, is_creation=True)
            if not is_valid:
                raise DatastoreValidationError(
                    f"Invalid record data: {'; '.join(errors)}",
                    details={"errors": error_details},
                )
            await self._validate_user_reference_columns(ctx, record)

        if upsert:
            written = await self.record_repository.bulk_upsert_records(
                ctx, sanitized_records, user_id
            )
        else:
            written = await self.record_repository.bulk_create_records(
                ctx, sanitized_records, user_id
            )

        # The bulk repository methods return only a count, not the created rows,
        # so emit one INSERT event per submitted record using its primary key
        # when present. Schedules match on pod/table/operation, so this is
        # sufficient to fire on bulk inserts.
        for record in sanitized_records:
            record_id = record.get(ctx.primary_key_column) or record.get("id")
            await self._emit_record_event(
                ctx,
                str(record_id) if record_id is not None else "",
                DatastoreRecordOperation.INSERT,
                record,
                user_id,
            )

        return written

    async def bulk_update_records(
        self,
        ctx: TableContext,
        updates: list[dict[str, Any]],
        user_id: UUID,
        *,
        admin_mode: bool = False,
    ) -> int:
        await self._require_record_write(user_id=user_id, ctx=ctx)
        if not updates:
            return 0

        pk = ctx.primary_key_column
        count = 0

        for update in updates:
            pk_val = update.get(pk) or update.get("id")
            if pk_val is None:
                raise DatastoreValidationError(
                    f"Missing primary key '{pk}' or 'id' in update data"
                )

            payload = update.copy()
            payload.pop(pk, None)
            payload.pop("id", None)

            await self.update_record(ctx, pk_val, payload, user_id, admin_mode=admin_mode)
            count += 1

        return count

    async def bulk_delete_records(
        self,
        ctx: TableContext,
        record_ids: list[Any],
        user_id: UUID,
        *,
        admin_mode: bool = False,
    ) -> int:
        await self._require_record_write(user_id=user_id, ctx=ctx)
        count = 0
        for record_id in record_ids:
            try:
                await self.delete_record(ctx, record_id, user_id, admin_mode=admin_mode)
                count += 1
            except DatastoreRecordNotFoundError:
                continue
        return count
