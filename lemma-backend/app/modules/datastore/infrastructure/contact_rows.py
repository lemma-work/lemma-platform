"""Contact-owned tables: rows a pod keeps about each of its contacts.

A table marked contact-owned has a ``contact_id`` column and a row-level
policy: a session reading as a member sees every row, a session reading as a
contact sees that contact's rows, and a session that named nobody -- an
outsider, or code that forgot to say -- sees nothing. The policy is enforced by
the database, under the same NOBYPASSRLS query role that ad-hoc queries run as,
so a contact's read is held to it even if the code asking forgot to filter.

It is not per-user isolation (``enable_rls``), and a table cannot be both: one
says each member sees their own rows, the other says every member sees every
contact's.
"""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.ports import DatastoreSchemaPort
from app.modules.datastore.domain.row_security import (
    AUDIENCE_SETTING,
    CONTACT_COLUMN,
    CONTACT_SETTING,
    RowAudience,
    RowPrincipal,
)
from app.modules.datastore.infrastructure.rls_context import verify_rls_context
from app.modules.datastore.infrastructure.sql_identifiers import sanitize_identifier

#: The most rows one contact read returns.
MAX_CONTACT_ROWS = 200


def _policy_name(table_name: str) -> str:
    return f"{table_name}_contact_isolation"


def _policy_sql(schema_name: str, table_name: str) -> str:
    member = f"current_setting('{AUDIENCE_SETTING}', TRUE) = '{RowAudience.MEMBER}'"
    named = f"NULLIF(current_setting('{CONTACT_SETTING}', TRUE), '')::UUID"
    rule = f"({member} OR {CONTACT_COLUMN} = {named})"
    return (
        f'CREATE POLICY "{_policy_name(table_name)}" '
        f'ON "{schema_name}"."{table_name}" USING {rule} WITH CHECK {rule}'
    )


async def set_contact_owned(
    schema: DatastoreSchemaPort,
    pod_id: UUID,
    table_name: str,
    *,
    enable: bool,
    per_user: bool = False,
) -> None:
    """Install or remove the contact policy on an existing table.

    Enabling adds ``contact_id`` if the table has none, and indexes it. Rows
    already there keep a null contact, which no contact can see. Disabling
    drops the policy and leaves the column -- and leaves row security on when
    ``per_user`` says the table's other policy still needs it, which is what a
    PATCH that turns per-user isolation on as it turns this off arrives as.
    """
    schema_name = schema.get_schema_name(pod_id)
    table = sanitize_identifier(table_name)
    qualified = f'"{schema_name}"."{table}"'
    async with schema.session_factory() as session:
        async with session.begin():
            await session.execute(
                text(f'DROP POLICY IF EXISTS "{_policy_name(table)}" ON {qualified}')
            )
            if enable:
                for statement in _enable_statements(schema_name, table, qualified):
                    await session.execute(text(statement))
            elif not per_user:
                await session.execute(
                    text(f"ALTER TABLE {qualified} NO FORCE ROW LEVEL SECURITY")
                )
                await session.execute(
                    text(f"ALTER TABLE {qualified} DISABLE ROW LEVEL SECURITY")
                )


def _enable_statements(schema_name: str, table: str, qualified: str) -> list[str]:
    return [
        f'ALTER TABLE {qualified} ADD COLUMN IF NOT EXISTS "{CONTACT_COLUMN}" UUID',
        (
            f'CREATE INDEX IF NOT EXISTS "{table}_contact_id_idx" '
            f'ON {qualified} ("{CONTACT_COLUMN}")'
        ),
        f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY",
        f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY",
        _policy_sql(schema_name, table),
    ]


async def read_contact_rows(
    schema: DatastoreSchemaPort,
    pod_id: UUID,
    table_name: str,
    contact_id: UUID,
    *,
    columns: Sequence[str],
    primary_key: str,
    limit: int,
    offset: int = 0,
) -> list[dict[str, object]]:
    """One contact's rows of a contact-owned table, as the query role.

    Only ``columns`` -- the ones the member chose to show contacts -- in
    primary-key order, so paging with ``offset`` neither repeats nor skips.

    Filtered twice: by the ``WHERE`` here, and by the policy the session names
    the contact to. Either alone would do; the second is what holds if the
    first is ever wrong.
    """
    await schema.ensure_query_role()
    schema_name = schema.get_schema_name(pod_id)
    select_sql = _select_sql(schema_name, table_name, columns, primary_key)
    page = {
        "contact": contact_id,
        "limit": max(1, min(limit, MAX_CONTACT_ROWS)),
        "offset": max(0, offset),
    }
    try:
        return await _read(schema, select_sql, contact_id, page)
    except DBAPIError:
        if not await schema.heal_query_role_access(schema_name):
            raise
    return await _read(schema, select_sql, contact_id, page)


def _select_sql(
    schema_name: str, table_name: str, columns: Sequence[str], primary_key: str
) -> str:
    table = sanitize_identifier(table_name)
    chosen = ", ".join(f'"{sanitize_identifier(name)}"' for name in columns)
    order = sanitize_identifier(primary_key)
    return (
        f'SELECT {chosen} FROM "{schema_name}"."{table}" '
        f'WHERE "{CONTACT_COLUMN}" = :contact '
        f'ORDER BY "{order}" LIMIT :limit OFFSET :offset'
    )


async def _read(
    schema: DatastoreSchemaPort,
    select_sql: str,
    contact_id: UUID,
    page: dict[str, object],
) -> list[dict[str, object]]:
    query_role = sanitize_identifier(datastore_settings.datastore_query_role)
    principal = RowPrincipal.contact(contact_id)
    async with schema.session_factory() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.execute(
            text("SELECT set_config('statement_timeout', :ms, true)"),
            {"ms": str(datastore_settings.datastore_query_statement_timeout_ms)},
        )
        await schema.set_rls_context(session, principal)
        await session.execute(text(f'SET LOCAL ROLE "{query_role}"'))
        result = await session.execute(text(select_sql), page)
        rows = [dict(row._mapping) for row in result]
        await verify_rls_context(session, principal)
        return rows


async def delete_rows_of_contact(
    schema: DatastoreSchemaPort, pod_id: UUID, table_name: str, contact_id: UUID
) -> int:
    """Delete one contact's rows of a contact-owned table. How many went.

    As the schema's owner, with the session naming the contact, so the policy
    holds the delete to their rows even if the ``WHERE`` were ever wrong.
    """
    schema_name = schema.get_schema_name(pod_id)
    table = sanitize_identifier(table_name)
    async with schema.session_factory() as session:
        async with session.begin():
            await session.execute(
                text(f"SELECT set_config('{_SETTING}', :contact, true)"),
                {"contact": str(contact_id)},
            )
            result = await session.execute(
                text(
                    f'DELETE FROM "{schema_name}"."{table}" '
                    f'WHERE "{CONTACT_COLUMN}" = :contact'
                ),
                {"contact": contact_id},
            )
            return int(result.rowcount or 0)


async def read_rows_of_contact_after(
    schema: DatastoreSchemaPort,
    pod_id: UUID,
    table_name: str,
    contact_id: UUID,
    *,
    key_column: str,
    after_key: str | None,
    limit: int,
) -> list[dict[str, object]]:
    """A page of one contact's rows, in key order, after ``after_key``.

    Keyed rather than offset, so a row added or removed while an export is
    paged through never moves one from a page already read into the next.
    The key is compared as text because a table's key may be of any type.
    """
    await schema.ensure_query_role()
    schema_name = schema.get_schema_name(pod_id)
    table = sanitize_identifier(table_name)
    key = sanitize_identifier(key_column)
    query_role = sanitize_identifier(datastore_settings.datastore_query_role)
    after = "" if after_key is None else f'AND "{key}"::text > :after '
    async with schema.session_factory() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.execute(
            text(
                "SELECT set_config('app.current_user_id', :nobody, true), "
                "set_config('app.current_user_is_pod_admin', 'false', true), "
                f"set_config('{_SETTING}', :contact, true)"
            ),
            {"nobody": str(_NOBODY), "contact": str(contact_id)},
        )
        await session.execute(text(f'SET LOCAL ROLE "{query_role}"'))
        parameters: dict[str, object] = {
            "contact": contact_id,
            "limit": max(1, min(limit, MAX_CONTACT_ROWS)),
        }
        if after_key is not None:
            parameters["after"] = after_key
        result = await session.execute(
            text(
                f'SELECT * FROM "{schema_name}"."{table}" '
                f'WHERE "{CONTACT_COLUMN}" = :contact {after}'
                f'ORDER BY "{key}"::text LIMIT :limit'
            ),
            parameters,
        )
        return [dict(row._mapping) for row in result]
