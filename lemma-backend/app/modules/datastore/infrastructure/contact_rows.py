"""Contact-owned tables: rows a pod keeps about each of its contacts.

A table marked contact-owned has a ``contact_id`` column and a row-level
policy: a session that names no contact sees every row, the way members always
have, and a session that names one sees that contact's rows and nothing else.
The policy is enforced by the database, under the same NOBYPASSRLS query role
that ad-hoc queries run as, so a contact's read is held to it even if the code
asking forgot to filter.

It is not per-user isolation (``enable_rls``), and a table cannot be both: one
says each member sees their own rows, the other says every member sees every
contact's.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.domain.errors import DatastoreQueryError
from app.modules.datastore.domain.ports import DatastoreSchemaPort
from app.modules.datastore.infrastructure.sql_identifiers import sanitize_identifier

#: The column a contact-owned table names its contact in.
CONTACT_COLUMN = "contact_id"

#: The most rows one contact read returns.
MAX_CONTACT_ROWS = 200

_SETTING = "app.current_contact_id"

#: The member a contact's read is made as: nobody.
_NOBODY = UUID(int=0)


def _policy_name(table_name: str) -> str:
    return f"{table_name}_contact_isolation"


def _policy_sql(schema_name: str, table_name: str) -> str:
    named = f"NULLIF(current_setting('{_SETTING}', TRUE), '')"
    rule = f"({named} IS NULL OR {CONTACT_COLUMN} = {named}::UUID)"
    return (
        f'CREATE POLICY "{_policy_name(table_name)}" '
        f'ON "{schema_name}"."{table_name}" USING {rule} WITH CHECK {rule}'
    )


async def set_contact_owned(
    schema: DatastoreSchemaPort, pod_id: UUID, table_name: str, *, enable: bool
) -> None:
    """Install or remove the contact policy on an existing table.

    Enabling adds ``contact_id`` if the table has none, and indexes it. Rows
    already there keep a null contact, which no contact can see. Disabling
    drops the policy and leaves the column.
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
                await session.execute(
                    text(
                        f"ALTER TABLE {qualified} "
                        f'ADD COLUMN IF NOT EXISTS "{CONTACT_COLUMN}" UUID'
                    )
                )
                await session.execute(
                    text(
                        f'CREATE INDEX IF NOT EXISTS "{table}_contact_id_idx" '
                        f'ON {qualified} ("{CONTACT_COLUMN}")'
                    )
                )
                await session.execute(
                    text(f"ALTER TABLE {qualified} ENABLE ROW LEVEL SECURITY")
                )
                await session.execute(
                    text(f"ALTER TABLE {qualified} FORCE ROW LEVEL SECURITY")
                )
                await session.execute(text(_policy_sql(schema_name, table)))
            else:
                await session.execute(
                    text(f"ALTER TABLE {qualified} NO FORCE ROW LEVEL SECURITY")
                )
                await session.execute(
                    text(f"ALTER TABLE {qualified} DISABLE ROW LEVEL SECURITY")
                )


async def read_contact_rows(
    schema: DatastoreSchemaPort,
    pod_id: UUID,
    table_name: str,
    contact_id: UUID,
    *,
    limit: int,
    offset: int = 0,
) -> list[dict[str, object]]:
    """One contact's rows of a contact-owned table, as the query role.

    Filtered twice: by the ``WHERE`` here, and by the policy the session names
    the contact to. Either alone would do; the second is what holds if the
    first is ever wrong.
    """
    await schema.ensure_query_role()
    schema_name = schema.get_schema_name(pod_id)
    try:
        return await _read(schema, schema_name, table_name, contact_id, limit, offset)
    except DBAPIError:
        if not await schema.heal_query_role_access(schema_name):
            raise
    return await _read(schema, schema_name, table_name, contact_id, limit, offset)


async def _read(
    schema: DatastoreSchemaPort,
    schema_name: str,
    table_name: str,
    contact_id: UUID,
    limit: int,
    offset: int,
) -> list[dict[str, object]]:
    table = sanitize_identifier(table_name)
    query_role = sanitize_identifier(datastore_settings.datastore_query_role)
    async with schema.session_factory() as session:
        await session.execute(text("SET TRANSACTION READ ONLY"))
        await session.execute(
            text("SELECT set_config('statement_timeout', :ms, true)"),
            {"ms": str(datastore_settings.datastore_query_statement_timeout_ms)},
        )
        # Nobody as a member, nobody as an admin, this contact as the contact.
        await session.execute(
            text(
                "SELECT set_config('app.current_user_id', :nobody, true), "
                "set_config('app.current_user_is_pod_admin', 'false', true), "
                f"set_config('{_SETTING}', :contact, true)"
            ),
            {"nobody": str(_NOBODY), "contact": str(contact_id)},
        )
        await session.execute(text(f'SET LOCAL ROLE "{query_role}"'))
        result = await session.execute(
            text(
                f'SELECT * FROM "{schema_name}"."{table}" '
                f'WHERE "{CONTACT_COLUMN}" = :contact '
                "LIMIT :limit OFFSET :offset"
            ),
            {
                "contact": contact_id,
                "limit": max(1, min(limit, MAX_CONTACT_ROWS)),
                "offset": max(0, offset),
            },
        )
        rows = [dict(row._mapping) for row in result]
        named = await session.scalar(
            text(f"SELECT current_setting('{_SETTING}', true)")
        )
        if named != str(contact_id):
            raise DatastoreQueryError(
                "A contact read lost the contact it was scoped to"
            )
        return rows
