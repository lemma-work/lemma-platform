"""A pod's query role can read its tables because of how they were made.

Ad-hoc SQL runs as the pod's own query role (``pod_query_role_name``) so
row-level security applies and no other pod is reachable. Read access used to
come from a grant per table, repaired at every API boot by a sweep over every
pod schema ever created -- which grew into most of the boot. Now a pod schema
is born with its role, ``USAGE`` for it and a default privilege covering its
future tables, and a schema from before that -- with no role of its own --
heals on its first query.

Against real PostgreSQL, because the failure being prevented is the database's
answer: a schema without ``USAGE`` does not say "permission denied" to an
unqualified name on the ``search_path``, it says the table does not exist.
"""

from __future__ import annotations

import logging
import secrets
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from app.modules.datastore.infrastructure import pod_schema_memo
from app.modules.datastore.infrastructure.session import get_datastore_engine
from app.modules.datastore.infrastructure.query_role import (
    _is_transient_conflict,
    pod_query_role_name,
)
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.datastore.tests.e2e.harness import DatastoreApi
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e

_TABLE = {
    "name": "widgets",
    "enable_rls": False,
    "columns": [{"name": "label", "type": "TEXT", "required": True}],
}


@pytest.fixture
def schema_manager() -> SchemaManager:
    return SchemaManager()


async def _scalar(schema_manager: SchemaManager, sql: str, **params) -> object:
    async with schema_manager.session_factory() as session:
        return (await session.execute(text(sql), params)).scalar()


async def _role_can_read(
    schema_manager: SchemaManager, schema: str, table: str
) -> bool:
    role = pod_query_role_name(schema)
    if not await _scalar(
        schema_manager, "SELECT 1 FROM pg_roles WHERE rolname = :role", role=role
    ):
        return False
    return bool(
        await _scalar(
            schema_manager,
            "SELECT has_schema_privilege(CAST(:role AS text), CAST(:schema AS text), "
            "'USAGE') AND has_table_privilege(CAST(:role AS text), "
            "format('%I.%I', CAST(:schema AS text), CAST(:table AS text)), "
            "'SELECT')",
            role=role,
            schema=schema,
            table=table,
        )
    )


async def _make_legacy(schema_manager: SchemaManager, schema: str) -> None:
    """Take away everything a schema is born with, its role included, as one
    made before per-pod roles was. This process forgets it saw the role, as a
    process that never had would."""
    role = pod_query_role_name(schema)
    statements = (
        f'REVOKE USAGE ON SCHEMA "{schema}" FROM "{role}"',
        f'REVOKE SELECT ON ALL TABLES IN SCHEMA "{schema}" FROM "{role}"',
        (
            "ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER "
            f'IN SCHEMA "{schema}" REVOKE SELECT ON TABLES FROM "{role}"'
        ),
        f'DROP ROLE "{role}"',
    )

    async def revoked() -> bool:
        # A REVOKE racing another session's GRANT on the schema's tables
        # fails with "tuple concurrently updated": contention, not a refusal,
        # which the product's own grants retry too. Anything else is real.
        try:
            async with schema_manager.session_factory() as session:
                for statement in statements:
                    await session.execute(text(statement))
                await session.commit()
        except Exception as exc:
            if not _is_transient_conflict(exc):
                raise
            return False
        return True

    await eventually(
        label="the legacy schema's grants revoked", probe=revoked, done=bool
    )
    pod_schema_memo.forget(schema)


def _heals(caplog) -> list[dict]:
    return [
        record.msg
        for record in caplog.records
        if isinstance(record.msg, dict)
        and record.msg.get("event") == "datastore.query_role.schema_healed"
    ]


async def test_a_new_table_is_queryable_with_no_grant_of_its_own(
    pod_api: DatastoreApi, schema_manager: SchemaManager
):
    await pod_api.create_table(_TABLE)
    await pod_api.create_record("widgets", {"label": "first"})
    schema = schema_manager.get_schema_name(pod_api.pod_id)

    assert await _role_can_read(schema_manager, schema, "widgets"), (
        "a table created after its schema is not readable by the query role"
    )
    result = await pod_api.query("SELECT label FROM widgets")
    assert [row["label"] for row in result["items"]] == ["first"], result


async def test_a_legacy_schema_heals_on_its_first_query_and_only_then(
    pod_api: DatastoreApi, schema_manager: SchemaManager, caplog
):
    caplog.set_level(logging.INFO)
    await pod_api.create_table(_TABLE)
    await pod_api.create_record("widgets", {"label": "kept"})
    schema = schema_manager.get_schema_name(pod_api.pod_id)
    await _make_legacy(schema_manager, schema)
    assert not await _role_can_read(schema_manager, schema, "widgets")

    first = await pod_api.query("SELECT label FROM widgets")
    second = await pod_api.query("SELECT count(*) AS n FROM widgets")

    assert [row["label"] for row in first["items"]] == ["kept"], first
    assert second["items"] == [{"n": 1}], second
    assert len(_heals(caplog)) == 1, "a healed schema was healed again"
    assert await _role_can_read(schema_manager, schema, "widgets")


async def test_a_table_added_to_a_healed_schema_needs_no_second_heal(
    pod_api: DatastoreApi, schema_manager: SchemaManager, caplog
):
    """The heal restores the schema's default privilege, not only its tables."""
    caplog.set_level(logging.INFO)
    await pod_api.create_table(_TABLE)
    schema = schema_manager.get_schema_name(pod_api.pod_id)
    await _make_legacy(schema_manager, schema)
    await pod_api.query("SELECT 1 AS one FROM widgets")

    await pod_api.create_table({**_TABLE, "name": "gadgets"})
    await pod_api.query("SELECT label FROM gadgets")

    assert len(_heals(caplog)) == 1
    assert await _role_can_read(schema_manager, schema, "gadgets")


async def test_a_query_naming_a_missing_table_is_refused_without_a_grant(
    pod_api: DatastoreApi, caplog
):
    caplog.set_level(logging.INFO)
    await pod_api.create_table(_TABLE)

    response = await pod_api.request(
        "POST",
        f"/pods/{pod_api.pod_id}/datastore/query",
        json={"query": "SELECT * FROM no_such_table"},
    )

    assert response.status_code >= 400, response.text
    assert _heals(caplog) == []


async def test_a_heal_leaves_the_change_log_unreadable(
    pod_api: DatastoreApi, schema_manager: SchemaManager
):
    """``GRANT ... ON ALL TABLES`` covers the change log, which carries rows a
    per-user policy hides; the heal must take it back, and a typo afterwards
    must not read the withheld log as missing access and heal again."""
    await pod_api.create_table(_TABLE)
    await pod_api.create_record("widgets", {"label": "logged"})
    schema = schema_manager.get_schema_name(pod_api.pod_id)
    await _make_legacy(schema_manager, schema)
    await pod_api.query("SELECT label FROM widgets")

    assert await _role_can_read(schema_manager, schema, "widgets")
    assert not await _role_can_read(schema_manager, schema, "reserved_changes")
    assert not await schema_manager.heal_query_role_access(schema)


async def test_a_createrole_app_role_can_enter_the_roles_it_makes_and_drop_them(
    pod_api: DatastoreApi, schema_manager: SchemaManager
):
    """The whole life of a pod's role, as an app role a managed Postgres hands
    out: ``CREATEROLE``, not a superuser, not the owner of anything yet.

    From PostgreSQL 16 such a role is made a member of each role it creates
    *without* the SET option, so asking whether it is a member says yes for a
    role ``SET LOCAL ROLE`` then refuses, and every query fails. The suite's
    own connection is a superuser, which may SET any role, so only a login like
    this one shows it. (``pod_api`` only for the stack it brings up.)
    """
    del pod_api
    admin = get_datastore_engine()
    login = f"lemma_app_{secrets.token_hex(6)}"
    password = secrets.token_hex(16)
    database = admin.url.database
    async with admin.begin() as conn:
        await conn.execute(
            text(
                f"CREATE ROLE \"{login}\" LOGIN PASSWORD '{password}' "
                "CREATEROLE NOSUPERUSER NOBYPASSRLS"
            )
        )
        await conn.execute(text(f'GRANT CREATE ON DATABASE "{database}" TO "{login}"'))
    engine = create_async_engine(
        admin.url.set(username=login, password=password),
        poolclass=NullPool,
        connect_args={"statement_cache_size": 0, "prepared_statement_cache_size": 0},
    )
    app = SchemaManager(engine=engine, session_factory=async_sessionmaker(engine))
    pod_id = uuid4()
    schema = app.get_schema_name(pod_id)
    role = pod_query_role_name(schema)
    try:
        await app.create_datastore_schema(pod_id)
        async with engine.begin() as conn:
            await conn.execute(text(f'CREATE TABLE "{schema}".widgets (label text)'))
            await conn.execute(text(f"INSERT INTO \"{schema}\".widgets VALUES ('x')"))
        pod_schema_memo.forget(schema)
        assert await app.ensure_query_role(schema)

        async with app.session_factory() as session:
            await session.execute(text(f'SET LOCAL ROLE "{role}"'))
            labels = (
                await session.execute(text(f'SELECT label FROM "{schema}".widgets'))
            ).scalars()
            assert list(labels) == ["x"]

        await app.drop_datastore_schema(pod_id)
        assert not await _scalar(
            schema_manager, "SELECT 1 FROM pg_roles WHERE rolname = :r", r=role
        ), "the app role could not drop the role it made"
    finally:
        await engine.dispose()
        async with admin.begin() as conn:
            await conn.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
            await conn.execute(text(f'DROP ROLE IF EXISTS "{role}"'))
            await conn.execute(text(f'DROP OWNED BY "{login}"'))
            await conn.execute(text(f'DROP ROLE "{login}"'))
