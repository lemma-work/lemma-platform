"""E2E: the pod toolset enforces agent grants against the real datastore.

Drives the in-process pod tools (pod_write_record / pod_get_records) with an
agent run context and asserts the grant model end-to-end: the pod default
assistant works with the user's permissions; a named agent is denied (and told
to request approval) until granted datastore.record.write, after which the write
goes through.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.models import (
    PodGetRecordsRequest,
    PodListFilesRequest,
    PodReadFileRequest,
    PodTablesRequest,
    PodWriteFileRequest,
    PodWriteRecordRequest,
    RecordFilter,
)
from app.modules.agent.tools.pod.pydantic_adapter import (
    pod_get_records,
    pod_list_files,
    pod_read_file,
    pod_tables,
    pod_write_file,
    pod_write_record,
)

pytestmark = pytest.mark.e2e


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"pod-tools-{uuid4().hex[:8]}",
            "description": "Pod toolset e2e",
            "organization_id": fixed_test_org["id"],
            "type": "HYBRID",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_table(authenticated_client, pod_id: str, table_name: str) -> None:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table_name,
            "primary_key_column": "id",
            "enable_rls": False,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
            ],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


async def _create_table_all_optional(
    authenticated_client, pod_id: str, table_name: str
) -> None:
    """A table whose only writable column is optional — so an empty payload would
    pass record validation and (before the fix) silently write a blank row."""
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table_name,
            "primary_key_column": "id",
            "enable_rls": False,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "note", "type": "TEXT", "required": False},
            ],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


async def _create_agent(authenticated_client, pod_id: str, name: str) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": name, "instruction": "Answer briefly."},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _grant(authenticated_client, pod_id, agent_name, table_name) -> None:
    response = await authenticated_client.put(
        f"/pods/{pod_id}/agents/{agent_name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "agent",
                    "resource_name": agent_name,
                    "permission_ids": ["agent.read"],
                },
                {
                    "resource_type": "datastore_table",
                    "resource_name": table_name,
                    "permission_ids": [
                        "datastore.table.read",
                        "datastore.record.read",
                        "datastore.record.write",
                    ],
                },
            ]
        },
    )
    assert response.status_code == status.HTTP_200_OK, response.text


def _run_ctx(*, user_id, pod_id, workload_id, agent_name):
    return SimpleNamespace(
        deps=BaseAgentContext(
            user_id=UUID(user_id),
            pod_id=UUID(pod_id),
            conversation_id=uuid4(),
            workload_type="agent" if workload_id is not None else None,
            workload_id=UUID(workload_id) if workload_id is not None else None,
            agent_name=agent_name,
        )
    )


async def _create_enum_table(authenticated_client, pod_id: str, table_name: str) -> None:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table_name,
            "primary_key_column": "id",
            "enable_rls": False,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
                {
                    "name": "result",
                    "type": "ENUM",
                    "required": True,
                    "options": ["pass", "fail", "friction"],
                },
            ],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


@pytest.mark.asyncio
async def test_pod_get_records_in_operator_matches_any_of_a_list(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """The agent-facing `in` filter operator returns rows matching any value."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"notes_{uuid4().hex[:8]}"
    await _create_table(authenticated_client, pod_id, table)
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=None,
        agent_name="pod_default",
    )
    for title in ("alpha", "beta", "gamma"):
        created = await pod_write_record(
            ctx,
            PodWriteRecordRequest(
                action="create", table_name=table, data={"title": title}
            ),
        )
        assert created["success"] is True, created

    listed = await pod_get_records(
        ctx,
        PodGetRecordsRequest(
            table_name=table,
            filters=[RecordFilter(column="title", op="in", value=["alpha", "gamma"])],
        ),
    )
    assert listed["success"] is True, listed
    assert sorted(r["title"] for r in listed["records"]) == ["alpha", "gamma"]


@pytest.mark.asyncio
async def test_pod_tables_describe_surfaces_enum_options(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """Describing a table exposes an ENUM column's allowed values."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"audit_{uuid4().hex[:8]}"
    await _create_enum_table(authenticated_client, pod_id, table)
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=None,
        agent_name="pod_default",
    )

    described = await pod_tables(ctx, PodTablesRequest(table_name=table))
    assert described["success"] is True, described
    columns = {c["name"]: c for c in described["table"]["columns"]}
    assert columns["result"]["type"] == "ENUM"
    assert columns["result"]["options"] == ["pass", "fail", "friction"]
    # A non-enum column carries no options key.
    assert "options" not in columns["title"]


@pytest.mark.asyncio
async def test_pod_file_tools_report_me_alias_paths(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """Every pod file tool presents paths in the /me/... alias form."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=None,
        agent_name="pod_default",
    )

    written = await pod_write_file(
        ctx,
        PodWriteFileRequest(path="/me/e2e/report.md", content="hello"),
    )
    assert written["success"] is True, written
    assert written["path"] == "/me/e2e/report.md"

    read = await pod_read_file(ctx, PodReadFileRequest(path="/me/e2e/report.md"))
    assert read["success"] is True, read
    assert read["path"] == "/me/e2e/report.md"
    assert read["text"] == "hello"

    listed = await pod_list_files(ctx, PodListFilesRequest(path="/me/e2e"))
    assert listed["success"] is True, listed
    paths = [f["path"] for f in listed["files"]]
    assert "/me/e2e/report.md" in paths
    # Never the raw /{user-uuid}/... storage path.
    assert all(not p.startswith(f"/{fixed_test_user['id']}") for p in paths)


@pytest.mark.asyncio
async def test_pod_default_agent_creates_and_lists_records_with_user_permissions(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"notes_{uuid4().hex[:8]}"
    await _create_table(authenticated_client, pod_id, table)

    # Pod default assistant: no workload id -> runs with the user's permissions.
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=None,
        agent_name="pod_default",
    )

    created = await pod_write_record(
        ctx,
        PodWriteRecordRequest(
            action="create", table_name=table, data={"title": "first"}
        ),
    )
    assert created["success"] is True, created

    listed = await pod_get_records(ctx, PodGetRecordsRequest(table_name=table))
    assert listed["success"] is True
    titles = [r.get("title") for r in listed["records"]]
    assert "first" in titles


@pytest.mark.asyncio
async def test_pod_write_record_rejects_empty_data_and_writes_no_row(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    """Regression: an empty payload must be rejected and persist nothing, even on
    an all-optional table where the write would otherwise create a blank row."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"notes_{uuid4().hex[:8]}"
    await _create_table_all_optional(authenticated_client, pod_id, table)

    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=None,
        agent_name="pod_default",
    )

    rejected = await pod_write_record(
        ctx, PodWriteRecordRequest(action="create", table_name=table, data={})
    )
    assert rejected["success"] is False
    assert "non-empty" in rejected["error"]

    # Nothing was written.
    listed = await pod_get_records(ctx, PodGetRecordsRequest(table_name=table))
    assert listed["success"] is True
    assert listed["total"] == 0
    assert listed["records"] == []

    # A real payload persists and reads back.
    created = await pod_write_record(
        ctx,
        PodWriteRecordRequest(action="create", table_name=table, data={"note": "hi"}),
    )
    assert created["success"] is True, created

    listed = await pod_get_records(ctx, PodGetRecordsRequest(table_name=table))
    assert listed["total"] == 1
    assert listed["records"][0]["note"] == "hi"


@pytest.mark.asyncio
async def test_named_agent_create_record_gated_by_grant(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    table = f"orders_{uuid4().hex[:8]}"
    await _create_table(authenticated_client, pod_id, table)
    agent_name = f"writer_{uuid4().hex[:8]}"
    agent = await _create_agent(authenticated_client, pod_id, agent_name)

    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=agent["id"],
        agent_name=agent_name,
    )

    # Without a grant the write is denied and surfaced as needs_approval.
    denied = await pod_write_record(
        ctx,
        PodWriteRecordRequest(
            action="create", table_name=table, data={"title": "blocked"}
        ),
    )
    assert denied["success"] is False
    assert denied["code"] == "MISSING_WORKLOAD_RESOURCE_GRANT"
    assert denied["needs_approval"] is True
    assert denied["approval"]["tool_name"] == "pod_write_record"

    # After granting record.write, the same call succeeds.
    await _grant(authenticated_client, pod_id, agent_name, table)
    allowed = await pod_write_record(
        ctx,
        PodWriteRecordRequest(
            action="create", table_name=table, data={"title": "allowed"}
        ),
    )
    assert allowed["success"] is True, allowed
