"""Pod schemas as catalog objects: how one is born, and how the dead are found.

Pod schemas live in the datastore database, which Alembic does not manage and
which is not the database that says whether a pod still exists. Creation and
removal are therefore both done here, by name, against ``pg_namespace``.

Each schema has a query role of its own (``query_role``) that lives and dies
with it: made in the transaction that creates the schema, dropped in the one
that drops it. Roles are cluster-wide, so one left behind by a dropped schema
would outlive its pod for good.
"""

from __future__ import annotations

from sqlalchemy import text

from app.modules.datastore.infrastructure import pod_schema_memo
from app.modules.datastore.infrastructure.query_role import (
    POD_SCHEMA_RE,
    drop_pod_role,
    grant_at_birth,
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
    """Create the schema if absent, readable by its own query role from birth.

    The role and its access ride the creating transaction, so a schema never
    exists without them unless the deployment cannot create roles at all
    (``grant_at_birth`` is best-effort). Only on creation: the existence check
    is race-free under the lock, and re-issuing them for every table would
    write the catalog each time. ``grant`` is false for a caller that leaves the
    access to a heal on the schema's first query.
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
        await grant_at_birth(conn, schema_name)


#: Which DDL version each pod schema carries; see ``pod_schema_migrations``.
#: Defined here because dropping a schema has to forget its row.
VERSIONS_TABLE = "public.lemma_pod_schema_versions"


async def drop_pod_schema(conn, schema_name: str) -> None:
    """Drop one pod schema, everything in it, its query role, and its version.

    The version row goes in the same transaction. Left behind, it outlives the
    schema it describes: whatever walks the versions table -- change-log
    retention does, every hour -- then addresses tables that are gone, and a
    schema of the same name made again would be taken as already migrated.
    """
    if not POD_SCHEMA_RE.fullmatch(schema_name):
        raise ValueError(f"not a pod schema: {schema_name!r}")
    await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
    await drop_pod_role(conn, schema_name)
    if await conn.scalar(text(f"SELECT to_regclass('{VERSIONS_TABLE}') IS NOT NULL")):
        await conn.execute(
            text(f"DELETE FROM {VERSIONS_TABLE} WHERE schema_name = :name"),
            {"name": schema_name},
        )


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
        """Drop one pod schema and its role, in its own transaction."""
        if not POD_SCHEMA_RE.fullmatch(schema_name):
            raise ValueError(f"not a pod schema: {schema_name!r}")
        async with self._engine.begin() as conn:
            await drop_pod_schema(conn, schema_name)
        pod_schema_memo.forget(schema_name)
