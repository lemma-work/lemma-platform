"""Pod schemas as catalog objects: how one is born, and how the dead are found.

Pod schemas live in the datastore database, which Alembic does not manage and
which is not the database that says whether a pod still exists. Creation and
removal are therefore both done here, by name, against ``pg_namespace``.
"""

from __future__ import annotations

from sqlalchemy import text

from app.modules.datastore.infrastructure.query_role import (
    POD_SCHEMA_RE,
    schema_access_statements,
)


async def lock_schema_bootstrap(conn, schema_name: str) -> None:
    """Serialize every creator of one pod schema across processes.

    PostgreSQL's ``CREATE SCHEMA IF NOT EXISTS`` is not race-free: two
    concurrent transactions can both observe the namespace as absent and one
    later fails the ``pg_namespace.nspname`` unique index. The pod provisioner
    and first table write use separate transactions/processes, so they must
    share this transaction-scoped advisory lock.
    """
    await conn.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:schema_name))"),
        {"schema_name": schema_name},
    )


async def bootstrap_pod_schema(conn, schema_name: str, *, grant: bool) -> None:
    """Create the schema if absent, readable by the query role from birth.

    The access statements ride the creating transaction, so a schema never
    exists without them. Only on creation: the existence check is race-free
    under the lock, and re-issuing them for every table would write the catalog
    each time. ``grant`` is false when the role could not be ensured; such a
    schema heals on its first query instead.
    """
    await lock_schema_bootstrap(conn, schema_name)
    exists = await conn.scalar(
        text("SELECT EXISTS (SELECT 1 FROM pg_namespace WHERE nspname = :name)"),
        {"name": schema_name},
    )
    if exists:
        return
    await conn.execute(text(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"'))
    if grant:
        for statement in schema_access_statements(schema_name):
            await conn.execute(text(statement))


class PodSchemaCatalog:
    """Lists and drops pod schemas in the datastore database."""

    def __init__(self, engine) -> None:
        self._engine = engine

    async def list_pod_schemas(self, *, after: str, limit: int) -> list[str]:
        """One keyset page of pod schema names, in name order.

        Filtered to the exact pod naming in Python as well as by prefix in SQL:
        a schema that merely starts with ``pod_`` is not one this module made,
        and nothing here may drop it.
        """
        async with self._engine.connect() as conn:
            result = await conn.execute(
                text(
                    "SELECT nspname FROM pg_namespace "
                    "WHERE nspname LIKE 'pod\\_%' AND nspname > :after "
                    "ORDER BY nspname LIMIT :limit"
                ),
                {"after": after, "limit": limit},
            )
            return [str(name) for name in result.scalars()]

    async def drop_schema(self, schema_name: str) -> None:
        """Drop one pod schema and everything in it, in its own transaction."""
        if not POD_SCHEMA_RE.fullmatch(schema_name):
            raise ValueError(f"not a pod schema: {schema_name!r}")
        async with self._engine.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
