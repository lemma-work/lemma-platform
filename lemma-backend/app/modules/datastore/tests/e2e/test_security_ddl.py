"""E2E regression tests for dynamic-table DDL injection safety (Workstream B).

Injection attempts through computed-column ``expression`` and column ``default``
must be rejected with a 4xx and must not execute the injected DDL (the target
table survives). The read-only SQL guard is also pinned.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e


@pytest.fixture
async def pod_api(authenticated_client: AsyncClient, fixed_test_org) -> DatastoreApi:
    suffix = uuid4().hex[:8]
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"DDL Pod {suffix}",
            "slug": f"ddl-pod-{suffix}",
            "type": "ASSISTANT",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return DatastoreApi(authenticated_client, response.json()["id"])


class TestDdlInjectionSafety:
    @pytest.mark.asyncio
    async def test_computed_expression_injection_is_rejected(
        self, pod_api: DatastoreApi
    ):
        suffix = uuid4().hex[:8]
        victim = f"victim_{suffix}"
        await pod_api.create_table(
            {
                "name": victim,
                "enable_rls": False,
                "columns": [{"name": "qty", "type": "INTEGER"}],
            }
        )

        target = f"target_{suffix}"
        resp = await pod_api.request(
            "POST",
            f"/pods/{pod_api.pod_id}/datastore/tables",
            json={
                "name": target,
                "enable_rls": False,
                "columns": [
                    {"name": "qty", "type": "INTEGER"},
                    {
                        "name": "evil",
                        "type": "INTEGER",
                        "computed": True,
                        "expression": f"qty) STORED; DROP TABLE {victim}; --",
                    },
                ],
            },
        )
        assert resp.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ), resp.text
        # The injected DROP must not have run.
        await pod_api.get_table(victim, expected_status=status.HTTP_200_OK)

    @pytest.mark.asyncio
    async def test_default_value_injection_is_rejected(self, pod_api: DatastoreApi):
        suffix = uuid4().hex[:8]
        victim = f"victim_def_{suffix}"
        await pod_api.create_table(
            {
                "name": victim,
                "enable_rls": False,
                "columns": [{"name": "name", "type": "TEXT"}],
            }
        )

        table = f"defaults_{suffix}"
        await pod_api.create_table(
            {
                "name": table,
                "enable_rls": False,
                "columns": [{"name": "name", "type": "TEXT"}],
            }
        )
        # A string default with an embedded quote must be quoted/escaped, not
        # break out of the literal. We send it as a normal string default and
        # assert the column is created safely (or cleanly rejected), never that
        # the victim table is dropped.
        resp = await pod_api.request(
            "POST",
            f"/pods/{pod_api.pod_id}/datastore/tables/{table}/columns",
            json={
                "column": {
                    "name": "note",
                    "type": "TEXT",
                    "default": f"x'); DROP TABLE {victim}; --",
                }
            },
        )
        assert resp.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ), resp.text
        # Regardless of accept/reject, the injected DROP must not have executed.
        await pod_api.get_table(victim, expected_status=status.HTTP_200_OK)

    @pytest.mark.asyncio
    async def test_enum_check_constraint_rejects_out_of_options_default(
        self, pod_api: DatastoreApi
    ):
        suffix = uuid4().hex[:8]
        resp = await pod_api.request(
            "POST",
            f"/pods/{pod_api.pod_id}/datastore/tables",
            json={
                "name": f"enum_{suffix}",
                "enable_rls": False,
                "columns": [
                    {
                        "name": "status",
                        "type": "ENUM",
                        "options": ["open", "closed"],
                        "default": "invalid_option",
                    }
                ],
            },
        )
        assert resp.status_code in (
            status.HTTP_400_BAD_REQUEST,
            status.HTTP_422_UNPROCESSABLE_ENTITY,
        ), resp.text


class TestAdHocQueryCannotLeaveThePodSchema:
    """The second control on cross-pod isolation, and the only one Postgres owns.

    Ad-hoc SQL runs as a single role that holds SELECT on every pod's schema,
    so what keeps one pod's query out of another's tables is the
    ``search_path`` plus ``analyze_query`` refusing a schema-qualified name.
    One parser gap in that is exposure rather than a degraded error, so the
    plan is checked too — and this is the test that the plan really does name
    the schema PostgreSQL resolved, which no fake can establish.
    """

    @pytest.mark.asyncio
    async def test_a_plan_that_resolves_into_another_pod_is_refused(
        self,
        pod_api: DatastoreApi,
        authenticated_client: AsyncClient,
        fixed_test_org,
    ):
        from sqlalchemy import text

        from app.modules.datastore.domain.errors import DatastoreQueryError
        from app.modules.datastore.infrastructure.record_query_cost import (
            guard_query_plan,
        )
        from app.modules.datastore.infrastructure.session import (
            get_datastore_session_maker,
        )

        table_name = f"secrets_{uuid4().hex[:8]}"
        neighbour_suffix = uuid4().hex[:8]
        neighbour_response = await authenticated_client.post(
            "/pods",
            json={
                "name": f"Neighbour Pod {neighbour_suffix}",
                "slug": f"neighbour-pod-{neighbour_suffix}",
                "type": "ASSISTANT",
                "organization_id": fixed_test_org["id"],
            },
        )
        assert neighbour_response.status_code == status.HTTP_201_CREATED
        neighbour = DatastoreApi(authenticated_client, neighbour_response.json()["id"])
        await neighbour.create_table(
            {
                "name": table_name,
                "enable_rls": False,
                "columns": [{"name": "note", "type": "TEXT"}],
            }
        )

        def schema_of(pod_id: str) -> str:
            return f"pod_{pod_id.replace('-', '_')}"

        async with get_datastore_session_maker()() as session:
            # Stands in for a parser gap: a bare name, resolved by the
            # search_path in a schema that is not the caller's pod. Reaching
            # the planner at all means the first control has already failed.
            await session.execute(
                text(f'SET LOCAL search_path TO "{schema_of(neighbour.pod_id)}"')
            )
            with pytest.raises(DatastoreQueryError, match="outside this pod"):
                await guard_query_plan(
                    session,
                    f"SELECT note FROM {table_name}",
                    schema_name=schema_of(pod_api.pod_id),
                )

    @pytest.mark.asyncio
    async def test_a_plan_confined_to_the_caller_s_pod_is_planned_normally(
        self,
        pod_api: DatastoreApi,
    ):
        from sqlalchemy import text

        from app.modules.datastore.infrastructure.record_query_cost import (
            guard_query_plan,
        )
        from app.modules.datastore.infrastructure.session import (
            get_datastore_session_maker,
        )

        table_name = f"notes_{uuid4().hex[:8]}"
        await pod_api.create_table(
            {
                "name": table_name,
                "enable_rls": False,
                "columns": [{"name": "note", "type": "TEXT"}],
            }
        )
        schema_name = f"pod_{pod_api.pod_id.replace('-', '_')}"

        async with get_datastore_session_maker()() as session:
            await session.execute(text(f'SET LOCAL search_path TO "{schema_name}"'))
            await guard_query_plan(
                session,
                f"SELECT note FROM {table_name}",
                schema_name=schema_name,
            )
