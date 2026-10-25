"""A pod's ad-hoc SQL cannot reach another pod's tables, through a string or not.

``table_to_xml`` and ``query_to_xml`` take the table -- or a whole query -- as a
string, which the SQL parser never sees, and once read another pod's rows (in
any organization) as XML: every pod's queries ran as one shared role granted
``USAGE`` and ``SELECT`` on every pod schema, so the parser was the only thing
keeping a query inside its pod.

Now each pod's queries run as a role of that pod's own, which holds nothing on
any other pod's schema. The first two tests are about the database, not the
parser: they go around it entirely and ask PostgreSQL directly, because the
point is that the next function nobody thought to deny is refused too.
"""

from __future__ import annotations

import pytest
from fastapi import status
from httpx import AsyncClient
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.modules.datastore.config import datastore_settings
from app.modules.datastore.infrastructure.query_role import pod_query_role_name
from app.modules.datastore.infrastructure.schema_manager import SchemaManager
from app.modules.datastore.tests.e2e.harness import DatastoreApi, pod_payload

pytestmark = pytest.mark.e2e

_SECRET = "the other pod's secret"
_MINE = "the pod's own row"


async def _other_pod_with_a_secret(
    authenticated_client: AsyncClient, fixed_test_org
) -> DatastoreApi:
    response = await authenticated_client.post(
        "/pods", json=pod_payload(fixed_test_org["id"])
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    other = DatastoreApi(authenticated_client, response.json()["id"])
    await other.create_table(
        {
            "name": "secrets",
            "enable_rls": False,
            "columns": [{"name": "value", "type": "TEXT", "required": True}],
        }
    )
    await other.create_record("secrets", {"value": _SECRET})
    return other


async def _pod_with_a_row(pod_api: DatastoreApi) -> None:
    await pod_api.create_table(
        {
            "name": "notes",
            "enable_rls": False,
            "columns": [{"name": "value", "type": "TEXT", "required": True}],
        }
    )
    await pod_api.create_record("notes", {"value": _MINE})


async def test_a_pods_query_role_holds_nothing_on_another_pod(
    pod_api: DatastoreApi, authenticated_client: AsyncClient, fixed_test_org
):
    """The premise, inverted: without the parser, the database stops it."""
    await _pod_with_a_row(pod_api)
    other = await _other_pod_with_a_secret(authenticated_client, fixed_test_org)
    schema_manager = SchemaManager()
    mine = schema_manager.get_schema_name(pod_api.pod_id)
    theirs = schema_manager.get_schema_name(other.pod_id)
    async with schema_manager.session_factory() as session:
        privileges = (
            await session.execute(
                text(
                    "SELECT has_schema_privilege(CAST(:role AS text), "
                    "CAST(:theirs AS text), 'USAGE'), "
                    "has_table_privilege(CAST(:role AS text), "
                    "format('%I.secrets', CAST(:theirs AS text)), 'SELECT'), "
                    "has_table_privilege(CAST(:role AS text), "
                    "format('%I.notes', CAST(:mine AS text)), 'SELECT'), "
                    "EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :shared) "
                    "AND (has_schema_privilege(CAST(:shared AS text), "
                    "CAST(:theirs AS text), 'USAGE') "
                    "OR has_table_privilege(CAST(:shared AS text), "
                    "format('%I.secrets', CAST(:theirs AS text)), 'SELECT'))"
                ),
                {
                    "role": pod_query_role_name(mine),
                    "mine": mine,
                    "theirs": theirs,
                    "shared": datastore_settings.datastore_query_role,
                },
            )
        ).one()
    other_usage, other_select, own_select, shared_reaches = privileges
    assert own_select, "the pod's role cannot read its own pod: the test is vacuous"
    assert not other_usage, "a pod's query role can use another pod's schema"
    assert not other_select, "a pod's query role can read another pod's table"
    assert not shared_reaches, "the old shared role still reaches a pod schema"


@pytest.mark.parametrize(
    "reach",
    [
        "SELECT table_to_xml('{schema}.secrets', true, false, '') AS x",
        "SELECT table_to_xml({oid}::oid::regclass, true, false, '') AS x",
        "SELECT query_to_xml('select value from {schema}.secrets', true, false, '')",
    ],
    ids=["by-name", "by-oid", "by-query"],
)
async def test_the_database_refuses_an_unparsed_cross_pod_read(
    pod_api: DatastoreApi,
    authenticated_client: AsyncClient,
    fixed_test_org,
    reach: str,
):
    """Straight to PostgreSQL as the pod's role: no parser, no plan check.

    By oid as well as by name, because a table reached by oid skips the
    schema's ``USAGE`` check -- only ``SELECT`` on the table itself stands.
    """
    await _pod_with_a_row(pod_api)
    other = await _other_pod_with_a_secret(authenticated_client, fixed_test_org)
    schema_manager = SchemaManager()
    mine = schema_manager.get_schema_name(pod_api.pod_id)
    theirs = schema_manager.get_schema_name(other.pod_id)
    assert await schema_manager.ensure_query_role(mine)
    async with schema_manager.session_factory() as session:
        oid = (
            await session.execute(
                text("SELECT to_regclass(:name)::oid"),
                {"name": f'"{theirs}".secrets'},
            )
        ).scalar()
        await session.execute(text(f'SET LOCAL ROLE "{pod_query_role_name(mine)}"'))
        own = (
            await session.execute(
                text(f"SELECT table_to_xml('{mine}.notes', true, false, '')")
            )
        ).scalar()
        assert _MINE in own, "the pod's role cannot read its own pod"
        with pytest.raises(DBAPIError) as refused:
            await session.execute(text(reach.format(schema=theirs, oid=oid)))
    sqlstate = getattr(refused.value.orig, "sqlstate", None)
    assert sqlstate == "42501", f"refused, but not by a privilege: {refused.value}"


@pytest.mark.parametrize(
    "template",
    [
        "SELECT table_to_xml('{schema}.secrets', true, false, '') AS x",
        "SELECT query_to_xml('select value from {schema}.secrets', true, false, '') AS x",
        "SELECT schema_to_xml('{schema}', true, false, '') AS x",
    ],
)
async def test_a_string_naming_another_pods_table_is_refused(
    pod_api: DatastoreApi,
    authenticated_client: AsyncClient,
    fixed_test_org,
    template: str,
):
    other = await _other_pod_with_a_secret(authenticated_client, fixed_test_org)
    schema = SchemaManager().get_schema_name(other.pod_id)

    response = await pod_api.request(
        "POST",
        f"/pods/{pod_api.pod_id}/datastore/query",
        json={"query": template.format(schema=schema)},
    )

    assert response.status_code == status.HTTP_400_BAD_REQUEST, response.text
    assert _SECRET not in response.text
