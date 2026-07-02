"""Holistic workload permissions: datastore tables / records.

Covers, for both AGENT and FUNCTION workloads:

* a table grant (``datastore.table.read`` + ``datastore.record.read`` +
  ``datastore.record.write``) authorizes full record CRUD on that table only;
* table grants are EXACT-MATCH (no cascade): a grant on table A gives no access
  to table B;
* data writes require ``datastore.record.write`` — ``datastore.table.update``
  (schema-only) does not authorize record writes;
* RLS ``mode=ADMIN`` requires table-admin (``datastore.table.delete``); a
  workload without it is rejected (not silently scoped);
* the query endpoint requires ``datastore.table.read`` on the referenced table;
* the DEFAULT POD AGENT ("user-resolved") gets record access with no grant.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status

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

pytestmark = pytest.mark.e2e


def _table_payload(name: str, *, enable_rls: bool) -> dict:
    return {
        "name": name,
        "primary_key_column": "id",
        "enable_rls": enable_rls,
        "columns": [
            {"name": "id", "type": "UUID", "required": True, "auto": True},
            {"name": "title", "type": "TEXT", "required": True},
        ],
    }


def _table_grant(name: str, *permissions: str) -> dict:
    return {
        "resource_type": "datastore_table",
        "resource_name": name,
        "permission_ids": list(permissions),
    }


async def _list_records_response(api: DatastoreApi, table: str, *, mode: str | None = None):
    """Raw list-records request (the harness ``list_records`` hardcodes 200, so
    use this when asserting a denial)."""
    params = {"mode": mode} if mode else None
    return await api.request(
        "GET", f"/pods/{api.pod_id}/datastore/tables/{table}/records", params=params
    )


# --------------------------------------------------------------------------- #
# Full record CRUD on granted table; exact-match (no cascade) to ungranted
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("workload_type", [AGENT, FUNCTION])
async def test_table_grant_authorizes_crud_and_is_exact_match(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    workload_type,
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    suffix = uuid4().hex[:8]
    granted, ungranted = f"granted_{suffix}", f"ungranted_{suffix}"
    await owner.create_table(_table_payload(granted, enable_rls=False))
    await owner.create_table(_table_payload(ungranted, enable_rls=False))

    name = f"tbl_{workload_type}_{suffix}"
    workload = await create_workload(authenticated_client, pod_id, workload_type, name)
    await replace_workload_grants(
        authenticated_client,
        pod_id,
        workload_type,
        name,
        [
            _table_grant(
                granted,
                "datastore.table.read",
                "datastore.record.read",
                "datastore.record.write",
            )
        ],
    )

    client = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=workload_type,
        workload_id=workload["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    api = DatastoreApi(client, pod_id)
    try:
        # Full CRUD on the granted table.
        created = await api.create_record(granted, {"title": "from-workload"})
        record_id = created["id"]
        listed = await api.list_records(granted)
        assert listed["total"] == 1
        assert (await api.get_record(granted, record_id))["title"] == "from-workload"
        await api.update_record(granted, record_id, {"title": "edited"})
        assert (await api.get_record(granted, record_id))["title"] == "edited"
        await api.delete_record(granted, record_id)

        # Exact-match: the grant on `granted` gives NO access to `ungranted`.
        denied = await api.create_record(
            ungranted, {"title": "nope"}, expected_status=status.HTTP_403_FORBIDDEN
        )
        assert denied == {}
        denied_list = await _list_records_response(api, ungranted)
        assert denied_list.status_code == status.HTTP_403_FORBIDDEN, denied_list.text
        assert denied_list.json()["code"] == "MISSING_WORKLOAD_RESOURCE_GRANT"
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# Writes need record.write, not table.update
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
@pytest.mark.parametrize("workload_type", [AGENT, FUNCTION])
async def test_record_write_requires_record_write_not_table_update(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    workload_type,
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    suffix = uuid4().hex[:8]
    table = f"shared_{suffix}"
    await owner.create_table(_table_payload(table, enable_rls=False))

    name = f"wperm_{workload_type}_{suffix}"
    workload = await create_workload(authenticated_client, pod_id, workload_type, name)
    await replace_workload_grants(
        authenticated_client,
        pod_id,
        workload_type,
        name,
        [_table_grant(table, "datastore.table.read", "datastore.table.update")],
    )

    client = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=workload_type,
        workload_id=workload["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    api = DatastoreApi(client, pod_id)
    try:
        # Schema permission does not grant data write.
        denied = await api.create_record(
            table, {"title": "schema-only"}, expected_status=status.HTTP_403_FORBIDDEN
        )
        assert denied == {}

        # Swap table.update -> record.write: write now succeeds.
        await replace_workload_grants(
            authenticated_client,
            pod_id,
            workload_type,
            name,
            [_table_grant(table, "datastore.table.read", "datastore.record.write")],
        )
        created = await api.create_record(table, {"title": "now-allowed"})
        assert created["title"] == "now-allowed"
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# RLS admin mode requires table-admin (table.delete)
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_rls_admin_mode_requires_table_delete(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    suffix = uuid4().hex[:8]
    table = f"rls_{suffix}"
    await owner.create_table(_table_payload(table, enable_rls=True))

    name = f"rls_agent_{suffix}"
    workload = await create_workload(authenticated_client, pod_id, AGENT, name)
    await replace_workload_grants(
        authenticated_client,
        pod_id,
        AGENT,
        name,
        [
            _table_grant(
                table,
                "datastore.table.read",
                "datastore.record.read",
                "datastore.record.write",
            )
        ],
    )

    client = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=AGENT,
        workload_id=workload["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    api = DatastoreApi(client, pod_id)
    try:
        await api.create_record(table, {"title": "own-row"})
        # Default (USER) mode works.
        assert (await api.list_records(table))["total"] == 1
        # ADMIN mode without table.delete is rejected, not silently scoped.
        denied = await _list_records_response(api, table, mode="ADMIN")
        assert denied.status_code == status.HTTP_403_FORBIDDEN, denied.text
        assert denied.json()["code"] == "DATASTORE_ACCESS_DENIED"

        # Granting table.delete (table-admin) unlocks ADMIN mode.
        await replace_workload_grants(
            authenticated_client,
            pod_id,
            AGENT,
            name,
            [
                _table_grant(
                    table,
                    "datastore.table.read",
                    "datastore.record.read",
                    "datastore.record.write",
                    "datastore.table.delete",
                )
            ],
        )
        admin_listed = await api.list_records(table, mode="ADMIN")
        assert admin_listed["total"] == 1
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# Query endpoint requires table.read on the referenced table
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_query_requires_table_read(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    suffix = uuid4().hex[:8]
    table = f"q_{suffix}"
    await owner.create_table(_table_payload(table, enable_rls=False))

    name = f"q_agent_{suffix}"
    workload = await create_workload(authenticated_client, pod_id, AGENT, name)
    # record.read but NOT table.read.
    await replace_workload_grants(
        authenticated_client,
        pod_id,
        AGENT,
        name,
        [_table_grant(table, "datastore.record.read")],
    )

    client = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=AGENT,
        workload_id=workload["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    api = DatastoreApi(client, pod_id)
    try:
        await api.query(
            f'SELECT * FROM "{table}"', expected_status=status.HTTP_403_FORBIDDEN
        )
        # Add table.read -> query is authorized.
        await replace_workload_grants(
            authenticated_client,
            pod_id,
            AGENT,
            name,
            [_table_grant(table, "datastore.record.read", "datastore.table.read")],
        )
        result = await api.query(f'SELECT * FROM "{table}"')
        assert "items" in result or "rows" in result
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# Default pod agent — user-resolved record access without a workload grant
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_default_pod_agent_record_access_without_grant(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    suffix = uuid4().hex[:8]
    table = f"pod_{suffix}"
    await owner.create_table(_table_payload(table, enable_rls=False))

    client = await mint_default_pod_agent_client(
        test_app, user_id=fixed_test_user["id"], pod_id=pod_id
    )
    api = DatastoreApi(client, pod_id)
    try:
        created = await api.create_record(table, {"title": "by-default-agent"})
        assert created["title"] == "by-default-agent"
        assert (await api.list_records(table))["total"] == 1
    finally:
        await client.aclose()


# --------------------------------------------------------------------------- #
# Destructive actions (table delete) are gated for workloads by default
# --------------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_named_workload_table_delete_requires_explicit_grant(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """datastore.table.delete is destructive: a named workload cannot delete a
    table without an EXPLICIT grant of it (standing authority). No session
    approval is recorded, so the un-granted attempt is gated."""
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    suffix = uuid4().hex[:8]
    table = f"del_{suffix}"
    await owner.create_table(_table_payload(table, enable_rls=False))

    name = f"del_agent_{suffix}"
    workload = await create_workload(authenticated_client, pod_id, AGENT, name)
    # Read/write only — deliberately NOT table.delete.
    await replace_workload_grants(
        authenticated_client,
        pod_id,
        AGENT,
        name,
        [_table_grant(table, "datastore.table.read", "datastore.record.write")],
    )
    client = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=AGENT,
        workload_id=workload["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    api = DatastoreApi(client, pod_id)
    try:
        denied = await api.delete_table(table, expected_status=status.HTTP_403_FORBIDDEN)
        # Datastore wraps the authz denial; the underlying reason is the gate.
        assert denied  # a 403 body is returned
    finally:
        await client.aclose()

    # Grant table.delete explicitly -> standing authority, delete succeeds.
    await replace_workload_grants(
        authenticated_client,
        pod_id,
        AGENT,
        name,
        [
            _table_grant(
                table,
                "datastore.table.read",
                "datastore.record.write",
                "datastore.table.delete",
            )
        ],
    )
    granted = await mint_workload_client(
        test_app,
        user_id=fixed_test_user["id"],
        workload_type=AGENT,
        workload_id=workload["id"],
        pod_id=pod_id,
        workload_name=name,
    )
    api = DatastoreApi(granted, pod_id)
    try:
        await api.delete_table(table)  # 204
    finally:
        await granted.aclose()


@pytest.mark.asyncio
async def test_default_pod_agent_table_delete_is_gated_despite_user_admin(
    test_app,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """The default pod agent mirrors the invoking user's permissions — but NOT
    destructive ones. Even though the invoking user is a pod admin who can
    delete tables, the default agent is gated without a session approval."""
    pod_id = await create_pod(authenticated_client, fixed_test_org)
    owner = DatastoreApi(authenticated_client, pod_id)
    suffix = uuid4().hex[:8]
    table = f"pod_del_{suffix}"
    await owner.create_table(_table_payload(table, enable_rls=False))

    client = await mint_default_pod_agent_client(
        test_app, user_id=fixed_test_user["id"], pod_id=pod_id
    )
    api = DatastoreApi(client, pod_id)
    try:
        denied = await api.delete_table(table, expected_status=status.HTTP_403_FORBIDDEN)
        assert denied
    finally:
        await client.aclose()

    # The human user (not delegated) can still delete it directly.
    await owner.delete_table(table)
