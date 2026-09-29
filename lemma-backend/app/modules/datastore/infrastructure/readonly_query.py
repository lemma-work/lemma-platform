"""Ad-hoc read-only SQL against one pod schema, as the RLS-subject query role."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.errors import DatastoreDomainError
from app.modules.datastore.domain.ports import DatastoreSchemaPort
from app.modules.datastore.infrastructure.record_query_cost import guard_query_plan
from app.modules.datastore.infrastructure.rls_context import verify_rls_context
from app.modules.datastore.infrastructure.sql_identifiers import sanitize_identifier

#: What the query role sees on a pod schema it was never given: a qualified
#: name is refused (42501) and an unqualified one, looked up on a search_path
#: whose only schema it may not use, simply does not exist (42P01).
_ACCESS_SQLSTATES = frozenset({"42501", "42P01"})

QueryRows = tuple[list[dict[str, object]], int, bool]


def _is_access_error(exc: BaseException) -> bool:
    """Whether the database refused the role access, however it was wrapped.

    Planning maps its driver errors to domain errors before they get here, so
    the SQLSTATE is on the ``__cause__``, not on what was raised.
    """
    driver_error = exc if isinstance(exc, DBAPIError) else exc.__cause__
    if not isinstance(driver_error, DBAPIError):
        return False
    sqlstate = getattr(getattr(driver_error, "orig", None), "sqlstate", None)
    return sqlstate in _ACCESS_SQLSTATES


async def execute_readonly_query(
    schema_manager: DatastoreSchemaPort,
    pod_id: UUID,
    query: str,
    user_id: UUID,
    *,
    enable_rls: bool,
    is_pod_admin: bool,
) -> QueryRows:
    """Run a pre-validated query; repair a legacy pod schema's access once.

    The role is ensured lazily rather than at boot, and a pod schema created
    before access was granted by construction is healed the first time a query
    finds it unreadable -- then the query runs again, exactly once. The heal is
    outside the failed transaction, which is already aborted, and only happens
    when the catalog confirms the role really lacks access, so a query naming a
    table that does not exist costs a catalog read, not a grant.
    """
    await schema_manager.ensure_query_role()
    schema_name = schema_manager.get_schema_name(pod_id)
    try:
        return await _run(
            schema_manager, schema_name, query, user_id, enable_rls, is_pod_admin
        )
    except (DBAPIError, DatastoreDomainError) as exc:
        if not _is_access_error(exc):
            raise
        if not await schema_manager.heal_query_role_access(schema_name):
            raise
    return await _run(
        schema_manager, schema_name, query, user_id, enable_rls, is_pod_admin
    )


async def _run(
    schema_manager: DatastoreSchemaPort,
    schema_name: str,
    query: str,
    user_id: UUID,
    enable_rls: bool,
    is_pod_admin: bool,
) -> QueryRows:
    max_rows = datastore_settings.datastore_query_max_rows
    query_role = sanitize_identifier(datastore_settings.datastore_query_role)
    async with schema_manager.session_factory() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.execute(
            text("SELECT set_config('statement_timeout', :ms, true)"),
            {"ms": str(datastore_settings.datastore_query_statement_timeout_ms)},
        )
        # All SETs are transaction-local so nothing leaks back to the pool.
        await session.execute(text(f'SET LOCAL search_path TO "{schema_name}"'))

        if enable_rls:
            await schema_manager.set_rls_context(
                session, user_id, is_pod_admin=is_pod_admin
            )

        # Run the user's SQL as the non-superuser, NOBYPASSRLS role so RLS
        # policies are enforced (the app's own connection bypasses RLS).
        # Set after the RLS-context GUCs above, which the policies read.
        await session.execute(text(f'SET LOCAL ROLE "{query_role}"'))

        await guard_query_plan(session, query, schema_name=schema_name)

        # Stream via a server-side cursor and pull at most max_rows + 1 so a
        # runaway result set never fully materializes in memory; the extra
        # row only tells us the result was truncated.
        result = await session.stream(text(query))
        rows: list[dict[str, object]] = []
        async for row in result:
            rows.append(dict(row._mapping))
            if len(rows) > max_rows:
                break
        await result.close()
        # The extra row is the only evidence the result was cut, and it
        # used to be dropped here -- so a caller was handed exactly
        # `max_rows` rows and a count equal to them, which reads as a
        # complete result. An agent then reports "you have 1000 orders"
        # to someone with forty thousand.
        truncated = len(rows) > max_rows
        if truncated:
            rows = rows[:max_rows]
        if enable_rls:
            await verify_rls_context(session, user_id, is_pod_admin=is_pod_admin)
        return rows, len(rows), truncated
