from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.domain.ports import DatastoreRecordRepositoryPort
from app.modules.datastore.domain.row_security import CONTACT_COLUMN, RowPrincipal
from app.modules.datastore.services.authorization import (
    DatastoreAuthorization,
)
from app.modules.datastore.infrastructure.record_bulk_delete import (
    bulk_delete_records as write_bulk_deletes,
)
from app.modules.datastore.infrastructure.record_bulk_update import (
    bulk_update_records as write_bulk_updates,
)
from app.modules.datastore.services.record_validator import (
    RecordValidator,
)
from app.modules.datastore.services.sql_introspection import analyze_query
from app.modules.datastore.services.record_idempotency import keyed_primary_key
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.record_query_scope import resolve_query_row_scope

if TYPE_CHECKING:
    from app.modules.datastore.changes.capture import ChangeCapture
    from app.modules.datastore.services.table_service import TableService
from app.modules.datastore.domain.write_intent import WriteIntent
from app.modules.datastore.services.record_user_references import (
    reject_unknown_user_references,
    user_references,
)
from app.modules.datastore.services.record_change_log import RecordChangeLog
from app.modules.identity.contracts import UserReader


class RecordService:
    def __init__(
        self,
        record_repository: DatastoreRecordRepositoryPort,
        authorization_service: object | None = None,
        user_repository: UserReader | None = None,
    ):
        self.record_repository = record_repository
        self.authorization_service = authorization_service
        self.authz = (
            DatastoreAuthorization(authorization_service)
            if authorization_service is not None
            else None
        )
        self.user_repository = user_repository
        self.change_log = RecordChangeLog()

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
        self.authz.refuse_contact_scoped_records(ctx)
        await self.authz.require_record_read(user_id=user_id, ctx=ctx)

    async def _require_record_write(
        self, *, user_id: UUID, ctx: TableContext, creates: bool = False
    ) -> None:
        if self.authz is None:
            return
        if not creates:
            self.authz.refuse_contact_scoped_records(ctx)
        await self.authz.require_record_write(user_id=user_id, ctx=ctx)

    def _for_contact(self, ctx: TableContext) -> TableContext:
        """``ctx`` for a create made for a contact, whose rows alone it may write."""
        contact_id = self.authz.contact_scope(ctx) if self.authz is not None else None
        return ctx if contact_id is None else replace(ctx, for_contact=contact_id)

    @staticmethod
    def _stamp_contact(ctx: TableContext, row: dict[str, Any]) -> dict[str, Any]:
        """A new row made for a contact names them, whatever the caller sent."""
        contact_id = ctx.for_contact
        return row if contact_id is None else {**row, CONTACT_COLUMN: str(contact_id)}

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

    def _user_references(
        self, ctx: TableContext, data: dict[str, Any]
    ) -> list[tuple[str, UUID]]:
        return user_references(self.user_repository, ctx, data)

    async def _reject_unknown_user_references(
        self, references: list[tuple[str, UUID]]
    ) -> None:
        await reject_unknown_user_references(self.user_repository, references)

    async def create_record(
        self,
        ctx: TableContext,
        data: dict[str, Any],
        user_id: UUID,
        *,
        intent: WriteIntent = WriteIntent.NORMAL,
        idempotency_key: str | None = None,
    ):
        """Insert one record; ``idempotency_key`` makes a repeat return the first
        (``services/record_idempotency``)."""
        await self._require_record_write(user_id=user_id, ctx=ctx, creates=True)
        ctx = self._for_contact(ctx)

        validator = RecordValidator(ctx)
        sanitized_data = self._stamp_contact(
            ctx, validator.strip_system_write_overrides(data)
        )
        if idempotency_key is not None:
            sanitized_data = keyed_primary_key(ctx, sanitized_data, idempotency_key)

        is_valid, errors, error_details = validator.validate(
            sanitized_data, is_creation=True
        )
        if not is_valid:
            raise DatastoreValidationError(
                f"Invalid record data: {'; '.join(errors)}",
                details={"errors": error_details},
            )

        await self._reject_unknown_user_references(
            self._user_references(ctx, sanitized_data)
        )
        changes = await self.change_log.capture(ctx, intent)
        record = await self.record_repository.create_record(
            ctx,
            sanitized_data,
            user_id,
            changes=changes,
            existing_on_conflict=idempotency_key is not None,
        )
        await self.change_log.after_commit(changes)
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

    async def _ensure_listing_index(self, table) -> None:
        await self.record_repository.ensure_listing_index(
            TableContext.from_table_entity(
                table,
                self.record_repository.schema_manager.get_schema_name(table.pod_id),
            )
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
    ) -> tuple[list[dict], int, bool]:
        """Validate, authorize, and run an ad-hoc read-only SQL query.

        Returns the rows, how many came back, and whether the row cap cut the
        result short. The third value matters: without it a capped result is
        indistinguishable from a complete one.

        Parses the statement (single, read-only, no cross-schema references) and
        enforces per-table ``DATASTORE_TABLE_READ`` for every referenced table via
        ``resolve_query_row_scope``. RLS-enabled and contact-owned tables are
        row-filtered at the database layer, as the principal ``ctx`` names: a
        member, a contact, or nobody.

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

        if not analysis.tables:
            # No registered table referenced (e.g. SELECT from a set-returning
            # function); fall back to a pod-level read check since there is no
            # per-table grant to authorize against.
            await self._require_datastore_read(user_id=user_id, pod_id=pod_id, ctx=ctx)

        is_pod_admin = await resolve_query_row_scope(
            pod_id=pod_id,
            table_names=analysis.tables,
            table_service=table_service,
            authz=self.authz,
            ctx=ctx,
            admin_mode=admin_mode,
            ensure_index=self._ensure_listing_index,
        )

        return await self.record_repository.execute_readonly_query(
            pod_id=pod_id,
            query=query,
            principal=RowPrincipal.of(ctx, is_pod_admin=is_pod_admin),
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
        expected_updated_at: datetime | None = None,
        intent: WriteIntent = WriteIntent.NORMAL,
    ):
        await self._require_record_write(user_id=user_id, ctx=ctx)
        enforce_user_scope = await self._should_enforce_user_scope(
            user_id=user_id,
            ctx=ctx,
            admin_mode=admin_mode,
        )
        changes = await self.change_log.capture(ctx, intent)
        record = await self._write_update(
            ctx,
            record_id,
            data,
            user_id,
            enforce_user_scope=enforce_user_scope,
            expected_updated_at=expected_updated_at,
            changes=changes,
        )
        await self.change_log.after_commit(changes)
        return record

    async def _write_update(
        self,
        ctx: TableContext,
        record_id: Any,
        data: dict[str, Any],
        user_id: UUID,
        *,
        enforce_user_scope: bool,
        changes: ChangeCapture,
        expected_updated_at: datetime | None = None,
    ):
        """Validate and write one row, without the per-caller preamble.

        Split so a bulk update pays the preamble once. Permission and row-scope
        are decided by the caller and the table, neither of which changes inside
        a loop, and re-asking cost a database round trip and a connection
        release per record.
        """
        sanitized_data = RecordValidator(ctx).strip_system_write_overrides(data)
        RecordValidator(ctx).validate_update(sanitized_data)
        await self._reject_unknown_user_references(
            self._user_references(ctx, sanitized_data)
        )
        return await self.record_repository.update_record(
            ctx,
            record_id,
            sanitized_data,
            user_id,
            changes=changes,
            enforce_user_scope=enforce_user_scope,
            expected_updated_at=expected_updated_at,
        )

    async def delete_record(
        self,
        ctx: TableContext,
        record_id: Any,
        user_id: UUID,
        *,
        admin_mode: bool = False,
        intent: WriteIntent = WriteIntent.NORMAL,
    ) -> bool:
        await self._require_record_write(user_id=user_id, ctx=ctx)
        enforce_user_scope = await self._should_enforce_user_scope(
            user_id=user_id,
            ctx=ctx,
            admin_mode=admin_mode,
        )
        changes = await self.change_log.capture(ctx, intent)
        await self.record_repository.delete_record(
            ctx,
            record_id,
            user_id,
            changes=changes,
            enforce_user_scope=enforce_user_scope,
        )
        await self.change_log.after_commit(changes)
        return True

    async def bulk_create_records(
        self,
        ctx: TableContext,
        records: list[dict[str, Any]],
        user_id: UUID,
        *,
        upsert: bool = False,
        intent: WriteIntent = WriteIntent.BULK,
    ):
        await self._require_record_write(user_id=user_id, ctx=ctx, creates=not upsert)
        if not records:
            return 0
        ctx = self._for_contact(ctx)

        validator = RecordValidator(ctx)
        sanitized_records = [
            self._stamp_contact(ctx, validator.strip_system_write_overrides(record))
            for record in records
        ]

        # Collected row by row so each row's own validation still runs in
        # order, then resolved once for the batch. The sibling below shared a
        # dedup set to the same end and only half solved it; see
        # `_reject_unknown_user_references`.
        user_references: list[tuple[str, UUID]] = []
        for record in sanitized_records:
            is_valid, errors, error_details = validator.validate(
                record, is_creation=True
            )
            if not is_valid:
                raise DatastoreValidationError(
                    f"Invalid record data: {'; '.join(errors)}",
                    details={"errors": error_details},
                )
            user_references.extend(self._user_references(ctx, record))
        await self._reject_unknown_user_references(user_references)

        # One change per written row, appended by the statement that wrote it
        # -- the same contract as a single create -- so the generated id and
        # anything the database defaulted are in it.
        changes = await self.change_log.capture(ctx, intent)

        write_records = (
            self.record_repository.bulk_upsert_records
            if upsert
            else self.record_repository.bulk_create_records
        )
        written = await write_records(
            ctx,
            sanitized_records,
            user_id,
            changes=changes,
        )
        await self.change_log.after_commit(changes)
        return written

    async def bulk_update_records(
        self,
        ctx: TableContext,
        updates: list[dict[str, Any]],
        user_id: UUID,
        *,
        admin_mode: bool = False,
        intent: WriteIntent = WriteIntent.BULK,
    ) -> int:
        await self._require_record_write(user_id=user_id, ctx=ctx)
        if not updates:
            return 0

        # Decided once, like `bulk_create_records` does with its permission
        # check. Looping over `update_record` re-ran the whole single-record
        # preamble per row -- a permission check against the database, a
        # connection release, and a row-scope decision -- none of which can
        # change inside the loop, because the caller, the table and the mode are
        # all fixed. That per-row cost is what dominated records/bulk/update.
        enforce_user_scope = await self._should_enforce_user_scope(
            user_id=user_id,
            ctx=ctx,
            admin_mode=admin_mode,
        )
        pk = ctx.primary_key_column
        prepared: list[tuple[Any, dict[str, Any]]] = []
        user_references: list[tuple[str, UUID]] = []

        for update in updates:
            pk_val = update.get(pk) or update.get("id")
            if pk_val is None:
                raise DatastoreValidationError(
                    f"Missing primary key '{pk}' or 'id' in update data"
                )

            payload = update.copy()
            payload.pop(pk, None)
            payload.pop("id", None)

            sanitized = RecordValidator(ctx).strip_system_write_overrides(payload)
            RecordValidator(ctx).validate_update(sanitized)
            user_references.extend(self._user_references(ctx, sanitized))
            prepared.append((pk_val, sanitized))

        await self._reject_unknown_user_references(user_references)

        changes = await self.change_log.capture(ctx, intent)
        count = await write_bulk_updates(
            self.record_repository,
            ctx,
            prepared,
            user_id,
            changes=changes,
            enforce_user_scope=enforce_user_scope,
        )
        # One wake for the batch, not one per row.
        await self.change_log.after_commit(changes)
        return count

    async def bulk_delete_records(
        self,
        ctx: TableContext,
        record_ids: list[Any],
        user_id: UUID,
        *,
        admin_mode: bool = False,
        intent: WriteIntent = WriteIntent.BULK,
    ) -> int:
        """Delete every named row, or none of them (`PS-DATA-013`).

        See `infrastructure/record_bulk_delete` for why this is one
        transaction and why an id that matched nothing is a refusal rather
        than a smaller count.
        """
        await self._require_record_write(user_id=user_id, ctx=ctx)
        if not record_ids:
            return 0
        # Decided once; see `bulk_update_records` for why.
        enforce_user_scope = await self._should_enforce_user_scope(
            user_id=user_id,
            ctx=ctx,
            admin_mode=admin_mode,
        )
        changes = await self.change_log.capture(ctx, intent)
        count = await write_bulk_deletes(
            self.record_repository,
            ctx,
            record_ids,
            user_id,
            changes=changes,
            enforce_user_scope=enforce_user_scope,
        )
        await self.change_log.after_commit(changes)
        return count
