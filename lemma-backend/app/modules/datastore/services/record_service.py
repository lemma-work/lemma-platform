from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import datetime
from functools import partial
from typing import TYPE_CHECKING, Any
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.domain.errors import DatastoreValidationError
from app.modules.datastore.domain.datastore_entities import DatastoreDataType
from app.modules.datastore.domain.ports import DatastoreRecordRepositoryPort
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.infrastructure.record_bulk_delete import (
    bulk_delete_records as write_bulk_deletes,
)
from app.modules.datastore.infrastructure.record_bulk_update import (
    bulk_update_records as write_bulk_updates,
)
from app.modules.datastore.services.record_validator import (
    RecordValidator,
    convert_record,
)
from app.modules.datastore.services.sql_introspection import analyze_query
from app.modules.datastore.services.table_context import TableContext
from app.modules.datastore.services.record_query_scope import resolve_query_row_scope

if TYPE_CHECKING:
    from app.modules.datastore.services.table_service import TableService
from app.modules.datastore.domain.events import DatastoreRecordOperation
from app.modules.datastore.services.record_events import RecordEventCoordinator
from app.modules.identity.contracts import UserReader


class RecordService:
    def __init__(
        self,
        record_repository: DatastoreRecordRepositoryPort,
        event_dispatcher: Callable[[], Awaitable[int]] | None = None,
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
        # The *platform* session, taken from the authorization service that
        # already reads through it. Not the datastore session: those are two
        # different databases, and it is the platform connection an API request
        # holds open while the record write runs against the other pool.
        self._platform_session = getattr(authorization_service, "session", None)
        self.events = RecordEventCoordinator(
            dispatcher=event_dispatcher, platform_session=self._platform_session
        )

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

    def _user_references(
        self, ctx: TableContext, data: dict[str, Any]
    ) -> list[tuple[str, UUID]]:
        """Every USER-typed value in one row, as ``(column, user id)``.

        In the row's own column order, because that is the order the error
        below reports in: the column a person is told about should be the first
        one they would find reading their own file.

        Empty when there is no user directory to check against. Collecting is
        the half that costs something -- it converts the row -- and a caller
        that cannot validate should not pay for it.
        """
        if self.user_repository is None:
            return []
        references: list[tuple[str, UUID]] = []
        for key, value in convert_record(ctx.columns, data, skip_auto=False).items():
            column = ctx.get_column(key)
            if column is None or column.type != DatastoreDataType.USER or value is None:
                continue
            references.append(
                (key, value if isinstance(value, UUID) else UUID(str(value)))
            )
        return references

    async def _reject_unknown_user_references(
        self, references: list[tuple[str, UUID]]
    ) -> None:
        """Confirm every collected reference names a real user, in one read.

        A dedup set made this cheap for the batch that names one person over
        and over, and left the batch that names a different person per row
        exactly as expensive -- one round trip per distinct id, which for an
        import is one per row. Asking which of the ids exist answers both in a
        single statement.

        Deactivated and deleted users still count as existing, as they did when
        this was ``user_repository.get``: a USER column records who something
        belongs to, and someone leaving must not make an old row unwritable.
        """
        if self.user_repository is None or not references:
            return
        existing = await self.user_repository.existing_ids(
            {user_id for _, user_id in references}
        )
        for key, user_id in references:
            if user_id not in existing:
                raise DatastoreValidationError(
                    f"User does not exist for column '{key}'"
                )

    async def create_record(
        self,
        ctx: TableContext,
        data: dict[str, Any],
        user_id: UUID,
    ):
        await self._require_record_write(user_id=user_id, ctx=ctx)

        validator = RecordValidator(ctx)
        sanitized_data = validator.strip_system_write_overrides(data)

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
        if ctx.events_enabled:
            event_factory = partial(
                self.events.required_for_record,
                ctx=ctx,
                operation=DatastoreRecordOperation.INSERT,
                user_id=user_id,
            )
        else:
            event_factory = None
        record = await self.record_repository.create_record(
            ctx,
            sanitized_data,
            user_id,
            event_factory=event_factory,
        )
        if event_factory is not None:
            await self.events.dispatch()
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
        ``resolve_query_row_scope``. RLS-enabled tables are row-filtered at the
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
        expected_updated_at: datetime | None = None,
    ):
        await self._require_record_write(user_id=user_id, ctx=ctx)
        enforce_user_scope = await self._should_enforce_user_scope(
            user_id=user_id,
            ctx=ctx,
            admin_mode=admin_mode,
        )
        record = await self._write_update(
            ctx,
            record_id,
            data,
            user_id,
            enforce_user_scope=enforce_user_scope,
            expected_updated_at=expected_updated_at,
        )
        if ctx.events_enabled:
            await self.events.dispatch()
        return record

    async def _write_update(
        self,
        ctx: TableContext,
        record_id: Any,
        data: dict[str, Any],
        user_id: UUID,
        *,
        enforce_user_scope: bool,
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
        event_factory = (
            partial(
                self.events.required_for_record,
                ctx=ctx,
                operation=DatastoreRecordOperation.UPDATE,
                user_id=user_id,
            )
            if ctx.events_enabled
            else None
        )
        return await self.record_repository.update_record(
            ctx,
            record_id,
            sanitized_data,
            user_id,
            enforce_user_scope=enforce_user_scope,
            event_factory=event_factory,
            expected_updated_at=expected_updated_at,
        )

    async def delete_record(
        self,
        ctx: TableContext,
        record_id: Any,
        user_id: UUID,
        *,
        admin_mode: bool = False,
    ) -> bool:
        await self._require_record_write(user_id=user_id, ctx=ctx)
        enforce_user_scope = await self._should_enforce_user_scope(
            user_id=user_id,
            ctx=ctx,
            admin_mode=admin_mode,
        )
        await self.record_repository.delete_record(
            ctx,
            record_id,
            user_id,
            enforce_user_scope=enforce_user_scope,
            event_factory=self._delete_event_factory(ctx, user_id),
        )
        if ctx.events_enabled:
            await self.events.dispatch()
        return True

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

        # One INSERT event per written row, built by the repository from the row
        # it actually wrote — the same contract as a single create. Building
        # them here from the submitted data would leave out the generated id and
        # anything the database defaulted, which a match condition may test.
        if ctx.events_enabled:
            event_factory = partial(
                self.events.required_for_record,
                ctx=ctx,
                operation=DatastoreRecordOperation.INSERT,
                user_id=user_id,
            )
        else:
            event_factory = None

        write_records = (
            self.record_repository.bulk_upsert_records
            if upsert
            else self.record_repository.bulk_create_records
        )
        written = await write_records(
            ctx,
            sanitized_records,
            user_id,
            event_factory=event_factory,
        )

        if event_factory is not None:
            await self.events.dispatch()

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

        event_factory = (
            partial(
                self.events.required_for_record,
                ctx=ctx,
                operation=DatastoreRecordOperation.UPDATE,
                user_id=user_id,
            )
            if ctx.events_enabled
            else None
        )
        count = await write_bulk_updates(
            self.record_repository,
            ctx,
            prepared,
            user_id,
            enforce_user_scope=enforce_user_scope,
            event_factory=event_factory,
        )

        # One dispatch for the batch, not one per row. Each row still stages its
        # own UPDATE event through the repository, so the event contract is
        # unchanged -- only the number of flushes is.
        if ctx.events_enabled:
            await self.events.dispatch()
        return count

    def _delete_event_factory(self, ctx: TableContext, user_id: UUID):
        """The DELETE event builder both delete paths stage their rows through."""
        if not ctx.events_enabled:
            return None
        return partial(
            self.events.required_for_record,
            ctx=ctx,
            operation=DatastoreRecordOperation.DELETE,
            user_id=user_id,
        )

    async def bulk_delete_records(
        self,
        ctx: TableContext,
        record_ids: list[Any],
        user_id: UUID,
        *,
        admin_mode: bool = False,
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
        count = await write_bulk_deletes(
            self.record_repository,
            ctx,
            record_ids,
            user_id,
            enforce_user_scope=enforce_user_scope,
            event_factory=self._delete_event_factory(ctx, user_id),
        )
        # One dispatch for the batch, as with bulk update: the repository
        # stages a DELETE event per row and flushes none of them.
        if ctx.events_enabled:
            await self.events.dispatch()
        return count
