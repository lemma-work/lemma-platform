"""The query role can read a pod's tables because of how they were made.

Ad-hoc SQL runs as ``datastore_query_role`` so row-level security applies. That
role used to get read access from a grant per table, repaired at every API boot
by a sweep over every pod schema ever created -- which grew into most of the
boot. Now a pod schema is born with ``USAGE`` and a default privilege covering
its future tables, and a schema from before that heals on its first query.

Against real PostgreSQL, because the failure being prevented is the database's
answer: a schema without ``USAGE`` does not say "permission denied" to an
unqualified name on the ``search_path``, it says the table does not exist.
"""

from __future__ import annotations

import logging

import pytest
from sqlalchemy import text

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.datastore.tests.e2e.harness import DatastoreApi

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
    return bool(
        await _scalar(
            schema_manager,
            "SELECT has_schema_privilege(CAST(:role AS text), CAST(:schema AS text), "
            "'USAGE') AND has_table_privilege(CAST(:role AS text), "
            "format('%I.%I', CAST(:schema AS text), CAST(:table AS text)), "
            "'SELECT')",
            role=datastore_settings.datastore_query_role,
            schema=schema,
            table=table,
        )
    )


async def _make_legacy(schema_manager: SchemaManager, schema: str) -> None:
    """Take away everything a schema is born with, as one made before was."""
    role = datastore_settings.datastore_query_role
    async with schema_manager.session_factory() as session:
        for statement in (
            f'REVOKE USAGE ON SCHEMA "{schema}" FROM "{role}"',
            f'REVOKE SELECT ON ALL TABLES IN SCHEMA "{schema}" FROM "{role}"',
            (
                "ALTER DEFAULT PRIVILEGES FOR ROLE CURRENT_USER "
                f'IN SCHEMA "{schema}" REVOKE SELECT ON TABLES FROM "{role}"'
            ),
        ):
            await session.execute(text(statement))
        await session.commit()


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
