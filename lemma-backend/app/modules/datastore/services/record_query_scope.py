"""Decide whose rows an ad-hoc query may see.

Split out of ``RecordService.execute_readonly_query`` so the method reads as
what it is — parse, authorize, run — and so the rule that decides row scope
sits in one place rather than interleaved with the loop that authorizes.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID

from app.core.authorization.context import Context
from app.modules.datastore.domain.datastore_entities import DatastoreTableEntity
from app.modules.datastore.domain.errors import DatastoreAccessDeniedError
from app.modules.datastore.services.authorization import DatastoreAuthorization
from app.modules.datastore.services.table_context import TableHydration
from app.modules.datastore.services.table_service import TableService


async def resolve_query_row_scope(
    *,
    pod_id: UUID,
    table_names: set[str],
    table_service: TableService,
    authz: DatastoreAuthorization | None,
    ctx: Context,
    admin_mode: bool,
    ensure_index: Callable[[DatastoreTableEntity], Awaitable[None]] | None = None,
) -> bool:
    """Authorize every referenced table and return whether to read as admin.

    ``DATASTORE_TABLE_READ`` is enforced per table, through the same
    ``ctx.require`` as a single-table read — what is batched is the lookup, in
    one statement rather than one per name.

    ``ensure_index`` is given the chance to back-fill each table's listing
    index before the query is planned. That matters more here than on the
    listing path: ``_reject_if_too_expensive`` refuses a query whose planned
    cost exceeds the ceiling, so an unindexed table does not merely make an
    ad-hoc query slow, it makes it fail.

    ``admin_mode`` is honored only when the caller administers *every*
    RLS-enabled table in the query: one session-wide flag governs all of them,
    so a partial answer would silently widen the tables the caller does not
    administer. Not administering all of them is an error rather than a quiet
    downgrade to scoped rows. A query touching no RLS table ignores the flag.
    """
    if not table_names:
        # A query naming no registered table (e.g. a set-returning function).
        # The caller has already fallen back to a pod-level read check, and
        # there is nothing here to look up, index or scope.
        return False
    tables = await table_service.get_tables(pod_id, sorted(table_names), ctx=ctx)
    if ensure_index is not None:
        for table in tables.values():
            await ensure_index(table)

    saw_rls = False
    admin_on_all_rls = True
    for table_name in sorted(table_names):
        table = tables[table_name]
        if not table.enable_rls:
            continue
        saw_rls = True
        if not admin_mode:
            continue
        if authz is None or not await authz.can_admin_table(
            pod_id=pod_id,
            table_id=table.id,
            ctx=ctx,
            hydration=TableHydration.of(table),
        ):
            admin_on_all_rls = False

    if not (admin_mode and saw_rls):
        return False
    if not admin_on_all_rls:
        raise DatastoreAccessDeniedError(
            "Admin mode requires permission to administer every "
            "RLS-enabled table referenced by the query."
        )
    return True
