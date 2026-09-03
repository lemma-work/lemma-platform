"""A named workload never exceeds the person who invoked it.

PS-ACCESS-020 states it twice: a workload acting for someone gets "the
intersection of that person's access and its own grants, and never the union",
and "if a workload holds a grant on a resource the invoking person cannot
reach, then the system shall refuse the access".

The implementation used to be grant-first — the workload's grants were
standalone authority and the invoking person was consulted only for PERSONAL
ownership and org-scoped resources — so an agent granted
``datastore.record.write`` wrote for anyone who could start it, including a
POD_VIEWER who cannot write a record with their own hands. These tests pin both
halves of the intersection, because only one of them can be shown by a refusal:

* the ceiling refuses an action the workload IS granted and the person is not,
* and it leaves alone an action they both hold — a ceiling that denied
  everything would pass the first test and be useless.

The third test is the counterweight: the pod's default assistant is the
opposite shape (it mirrors its invoker and holds no grants at all), so it must
keep working with no grant anywhere in sight.

The workload tokens here are minted directly, which is what a surface, a
schedule or an agent tool does when it delegates. That matters for the promise
being tested: the ceiling has to hold on "every request the workload makes, not
only on the first", so it belongs in authorization rather than at the one door
where a run is started.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status
from httpx import AsyncClient

from app.modules.pod.tests.e2e.workload_permissions.harness import (
    AGENT,
    FUNCTION,
    DatastoreApi,
    create_pod,
    create_workload,
    mint_default_pod_agent_client,
    mint_workload_client,
    replace_workload_grants,
)
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    invite_org_member,
    signup_user,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


def _table_payload(name: str) -> dict:
    return {
        "name": name,
        "primary_key_column": "id",
        "enable_rls": False,
        "columns": [
            {"name": "id", "type": "UUID", "required": True, "auto": True},
            {"name": "title", "type": "TEXT", "required": True},
        ],
    }


def _full_table_grant(name: str) -> dict:
    """Everything the ledger table has to offer — read, and write."""
    return {
        "resource_type": "datastore_table",
        "resource_name": name,
        "permission_ids": [
            "datastore.table.read",
            "datastore.record.read",
            "datastore.record.write",
        ],
    }


@pytest.fixture
async def ceiling(authenticated_client, async_client, fixed_test_org):
    """A pod, a table with a row in it, and two members of different standing.

    ``viewer`` is a POD_VIEWER: reads records, writes none. ``writer`` is a
    POD_USER: does both. The difference between them is the whole experiment —
    one workload, one grant, two people driving it.
    """
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    table = f"ledger_{uuid4().hex[:8]}"
    await owner.create_table(_table_payload(table))
    await owner.create_record(table, {"title": "opening balance"})

    viewer = await signup_user(async_client, "ceiling-viewer")
    writer = await signup_user(async_client, "ceiling-writer")
    for user, role in ((viewer, "POD_VIEWER"), (writer, "POD_USER")):
        org_member = await invite_org_member(
            authenticated_client,
            async_client,
            org_id=fixed_test_org["id"],
            user=user,
        )
        await add_pod_member(
            authenticated_client,
            pod_id=pod_id,
            organization_member_id=org_member["id"],
            role=role,
            roles=[role],
        )

    return {"pod_id": pod_id, "table": table, "viewer": viewer, "writer": writer}


async def _granted_workload_client(
    test_app, owner_client: AsyncClient, ceiling: dict, workload_type: str, user: dict
) -> AsyncClient:
    """A workload granted full access to the ledger, delegating for ``user``."""
    name = f"ceil_{workload_type}_{uuid4().hex[:8]}"
    workload = await create_workload(
        owner_client, ceiling["pod_id"], workload_type, name
    )
    await replace_workload_grants(
        owner_client,
        ceiling["pod_id"],
        workload_type,
        name,
        [_full_table_grant(ceiling["table"])],
    )
    return await mint_workload_client(
        test_app,
        user_id=user["id"],
        workload_type=workload_type,
        workload_id=workload["id"],
        pod_id=ceiling["pod_id"],
        workload_name=name,
    )


@pytest.mark.parametrize("workload_type", [AGENT, FUNCTION])
async def test_a_workload_is_refused_what_its_invoker_cannot_do(
    test_app, authenticated_client, ceiling, workload_type
):
    """The grant says write; the person driving it may not. The grant loses."""
    client = await _granted_workload_client(
        test_app, authenticated_client, ceiling, workload_type, ceiling["viewer"]
    )
    api = DatastoreApi(client, ceiling["pod_id"])
    try:
        response = await api.request(
            "POST",
            f"/pods/{ceiling['pod_id']}/datastore/tables/{ceiling['table']}/records",
            json={"data": {"title": "written by proxy"}},
        )
        assert response.status_code == status.HTTP_403_FORBIDDEN, response.text
        # A distinct code, because the two refusals need opposite fixes: this
        # one is not repaired by granting the workload more.
        assert response.json()["code"] == "DELEGATION_EXCEEDS_INVOKER", response.text
    finally:
        await client.aclose()

    # And the row is genuinely absent — the refusal is the write, not just the
    # status line coming back red after the fact.
    owner = DatastoreApi(authenticated_client, ceiling["pod_id"])
    titles = {
        row["title"] for row in (await owner.list_records(ceiling["table"]))["items"]
    }
    assert "written by proxy" not in titles


@pytest.mark.parametrize("workload_type", [AGENT, FUNCTION])
async def test_a_workload_keeps_what_it_and_its_invoker_both_hold(
    test_app, authenticated_client, ceiling, workload_type
):
    """Intersection, not subtraction: what both sides hold still goes through.

    Two directions in one test on purpose. The POD_VIEWER can read records, so
    the same delegation that was refused the write reads perfectly well — the
    ceiling is per action, not a blanket "this person is junior". And the
    POD_USER, who holds the write, gets the write.
    """
    viewer_client = await _granted_workload_client(
        test_app, authenticated_client, ceiling, workload_type, ceiling["viewer"]
    )
    try:
        listed = await DatastoreApi(viewer_client, ceiling["pod_id"]).list_records(
            ceiling["table"]
        )
        assert listed["total"] == 1, listed
    finally:
        await viewer_client.aclose()

    writer_client = await _granted_workload_client(
        test_app, authenticated_client, ceiling, workload_type, ceiling["writer"]
    )
    try:
        created = await DatastoreApi(writer_client, ceiling["pod_id"]).create_record(
            ceiling["table"], {"title": "written for a pod user"}
        )
        assert created["title"] == "written for a pod user"
    finally:
        await writer_client.aclose()


async def test_the_default_pod_agent_still_mirrors_its_invoker(
    test_app, authenticated_client, ceiling
):
    """The assistant holds no grants at all, and must not be caught by this.

    It is the one workload whose authority IS its invoker's, so intersecting a
    grant set it does not have would deny it everything. It takes a different
    branch in ``authorize`` and this test is what says so out loud.
    """
    client = await mint_default_pod_agent_client(
        test_app, user_id=ceiling["writer"]["id"], pod_id=ceiling["pod_id"]
    )
    try:
        created = await DatastoreApi(client, ceiling["pod_id"]).create_record(
            ceiling["table"], {"title": "written by the assistant"}
        )
        assert created["title"] == "written by the assistant"
    finally:
        await client.aclose()
