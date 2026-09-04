"""Function shape: lifecycle, authorization, and datastore writes.

The runtime journeys -- queueing, concurrency, timeouts, cancellation and
connector operations -- are in `test_function_execution_e2e.py`. The two were
one 200-second file, which was the floor for the whole Backend E2E workflow:
the shard planner could not split a file, and `--dist loadscope` groups a
module's functions onto one xdist worker, so no worker count could either.
Halves land on separate runners.

The line is what a test is *about*. Everything here is a claim about a
function's shape or who may touch it, and reaches the sandbox only far enough
to prove the claim.
"""

from __future__ import annotations

import time
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.modules.test_support.e2e.function_helpers import (
    bulk_facade_function_code,
    create_folder,
    create_function,
    create_table,
    function_payload,
    record_grant_function_code,
    replace_function_resource_grants,
    replace_role_resource_grants,
    run_function,
    typed_function_code,
    wait_for_run_completion,
)
from app.modules.test_support.e2e_authz import (
    create_role_visibility_context,
    item_names,
)

# `usefixtures` is deliberately NOT here. At module scope it made every test
# boot a uvicorn backend server and a local sandbox server, including the ones
# that never supply `code` and only assert status codes -- the duplicate-name
# check measured 18.8s in CI to prove a 409. It is applied per test below, to
# the ones that actually reach the runtime.
#
# `workspace` stays at module scope: it is what the sandbox shard's marker
# filter selects on, and moving it would change which shard these run in. Both
# halves of the split carry it for that reason, and a contract test
# (test_workspace_marked_files_are_routed_consistently_within_a_directory)
# fails if they ever disagree.
pytestmark = [
    pytest.mark.e2e,
    pytest.mark.workspace,
]


@pytest.mark.asyncio
async def test_create_function_rejects_duplicate_name_in_same_pod(
    authenticated_client,
    test_pod,
):
    pod_id = test_pod["id"]
    function_name = f"duplicate_function_{uuid4().hex[:8]}"

    first = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json=function_payload(function_name),
        follow_redirects=True,
    )
    assert first.status_code == status.HTTP_201_CREATED, first.text

    second = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json=function_payload(function_name),
        follow_redirects=True,
    )
    assert second.status_code == status.HTTP_409_CONFLICT, second.text
    assert second.json()["code"] == "FUNCTION_CONFLICT"


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_function_lifecycle(authenticated_client, test_pod):
    pod_id = test_pod["id"]
    func_name = f"func_{uuid4().hex[:8]}"

    code = f"""#input_type_name: UppercaseInput
#output_type_name: UppercaseResult
#function_name: {func_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class UppercaseInput(BaseModel):
    text: str

class UppercaseResult(BaseModel):
    result: str

async def {func_name}(ctx: FunctionContext, data: UppercaseInput) -> UppercaseResult:
    return UppercaseResult(result=data.text.upper())"""

    func = await create_function(
        authenticated_client,
        pod_id,
        {
            "name": func_name,
            "description": "Function CRUD smoke test",
            "code": code,
        },
    )
    assert func["name"] == func_name

    get_response = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{func_name}"
    )
    assert get_response.status_code == status.HTTP_200_OK, get_response.text
    assert get_response.json()["id"] == func["id"]

    list_response = await authenticated_client.get(f"/pods/{pod_id}/functions")
    assert list_response.status_code == status.HTTP_200_OK, list_response.text
    assert any(item["name"] == func_name for item in list_response.json()["items"])

    update_response = await authenticated_client.patch(
        f"/pods/{pod_id}/functions/{func_name}",
        json={"description": "Updated description"},
    )
    assert update_response.status_code == status.HTTP_200_OK, update_response.text
    assert update_response.json()["description"] == "Updated description"

    delete_response = await authenticated_client.delete(
        f"/pods/{pod_id}/functions/{func_name}"
    )
    assert delete_response.status_code == status.HTTP_200_OK, delete_response.text


@pytest.mark.asyncio
async def test_function_list_and_access_respects_pod_roles(
    authenticated_client,
    async_client,
    fixed_test_org,
):
    ctx = await create_role_visibility_context(
        authenticated_client,
        async_client,
        fixed_test_org,
        pod_name_prefix="function-visibility",
        custom_role="FUNCTION_REVIEWERS",
    )
    pod_id = ctx["pod_id"]
    default_name = f"default_func_{uuid4().hex[:8]}"
    editor_name = f"editor_func_{uuid4().hex[:8]}"
    custom_name = f"custom_func_{uuid4().hex[:8]}"

    await create_function(authenticated_client, pod_id, function_payload(default_name))
    await create_function(
        authenticated_client,
        pod_id,
        function_payload(editor_name, "RESTRICTED"),
    )
    await create_function(
        authenticated_client,
        pod_id,
        function_payload(custom_name, "RESTRICTED"),
    )

    editor_function = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{editor_name}",
    )
    assert editor_function.status_code == status.HTTP_200_OK, editor_function.text
    grant_response = await authenticated_client.put(
        f"/pods/{pod_id}/roles/POD_EDITOR/permissions",
        json={
            "grants": [
                {
                    "resource_type": "function",
                    "resource_name": editor_function.json()["name"],
                    "permission_ids": ["function.read", "function.update"],
                }
            ]
        },
    )
    assert grant_response.status_code == status.HTTP_200_OK, grant_response.text
    custom_function = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{custom_name}",
    )
    assert custom_function.status_code == status.HTTP_200_OK, custom_function.text
    custom_grant_response = await authenticated_client.put(
        f"/pods/{pod_id}/roles/{ctx['custom_role']}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "function",
                    "resource_name": custom_function.json()["name"],
                    "permission_ids": ["function.read"],
                }
            ]
        },
    )
    assert custom_grant_response.status_code == status.HTTP_200_OK, (
        custom_grant_response.text
    )

    viewer_list = await async_client.get(
        f"/pods/{pod_id}/functions",
        headers=ctx["viewer_headers"],
    )
    assert viewer_list.status_code == status.HTTP_200_OK, viewer_list.text
    assert item_names(viewer_list.json()) == {default_name}

    editor_list = await async_client.get(
        f"/pods/{pod_id}/functions",
        headers=ctx["editor_headers"],
    )
    assert editor_list.status_code == status.HTTP_200_OK, editor_list.text
    assert item_names(editor_list.json()) == {default_name, editor_name}
    editor_items = {item["name"]: item for item in editor_list.json()["items"]}
    assert set(editor_items[default_name]["allowed_actions"]) == {
        "function.read",
        "function.execute",
        "function.update",
    }
    assert set(editor_items[editor_name]["allowed_actions"]) == {
        "function.read",
        "function.update",
    }
    editor_get_default = await async_client.get(
        f"/pods/{pod_id}/functions/{default_name}",
        headers=ctx["editor_headers"],
    )
    assert editor_get_default.status_code == status.HTTP_200_OK, editor_get_default.text
    assert set(editor_get_default.json()["allowed_actions"]) == {
        "function.read",
        "function.execute",
        "function.update",
    }
    editor_get_restricted = await async_client.get(
        f"/pods/{pod_id}/functions/{editor_name}",
        headers=ctx["editor_headers"],
    )
    assert editor_get_restricted.status_code == status.HTTP_200_OK, (
        editor_get_restricted.text
    )
    assert set(editor_get_restricted.json()["allowed_actions"]) == {
        "function.read",
        "function.update",
    }

    custom_list = await async_client.get(
        f"/pods/{pod_id}/functions",
        headers=ctx["custom_headers"],
    )
    assert custom_list.status_code == status.HTTP_200_OK, custom_list.text
    assert item_names(custom_list.json()) == {default_name, custom_name}
    custom_items = {item["name"]: item for item in custom_list.json()["items"]}
    assert set(custom_items[default_name]["allowed_actions"]) == {"function.read"}
    assert set(custom_items[custom_name]["allowed_actions"]) == {"function.read"}
    custom_get_restricted = await async_client.get(
        f"/pods/{pod_id}/functions/{custom_name}",
        headers=ctx["custom_headers"],
    )
    assert custom_get_restricted.status_code == status.HTTP_200_OK, (
        custom_get_restricted.text
    )
    assert set(custom_get_restricted.json()["allowed_actions"]) == {"function.read"}

    viewer_get_restricted = await async_client.get(
        f"/pods/{pod_id}/functions/{editor_name}",
        headers=ctx["viewer_headers"],
    )
    assert viewer_get_restricted.status_code == status.HTTP_403_FORBIDDEN

    viewer_edit_default = await async_client.patch(
        f"/pods/{pod_id}/functions/{default_name}",
        json={"description": "viewer edit"},
        headers=ctx["viewer_headers"],
    )
    assert viewer_edit_default.status_code == status.HTTP_403_FORBIDDEN

    custom_edit_custom = await async_client.patch(
        f"/pods/{pod_id}/functions/{custom_name}",
        json={"description": "custom viewer edit"},
        headers=ctx["custom_headers"],
    )
    assert custom_edit_custom.status_code == status.HTTP_403_FORBIDDEN

    editor_edit_restricted = await async_client.patch(
        f"/pods/{pod_id}/functions/{editor_name}",
        json={"description": "editor edit"},
        headers=ctx["editor_headers"],
    )
    assert editor_edit_restricted.status_code == status.HTTP_200_OK
    assert set(editor_edit_restricted.json()["allowed_actions"]) == {
        "function.read",
        "function.update",
    }


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_function_execution_datastore_and_file_round_trip(
    authenticated_client,
    test_pod,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"store_file_{suffix}"
    table_name = f"expenses_{suffix}"
    folder_path = f"/function-grants-{suffix}"

    table = await create_table(
        authenticated_client,
        pod_id,
        table_name,
        visibility="RESTRICTED",
    )
    folder = await create_folder(
        authenticated_client,
        pod_id,
        folder_path,
        visibility="RESTRICTED",
    )

    code = f"""#input_type_name: SaveExpenseInput
#output_type_name: SaveExpenseResult
#function_name: {function_name}

from pathlib import Path
from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

class SaveExpenseInput(BaseModel):
    title: str
    note: str

class SaveExpenseResult(BaseModel):
    record_id: str
    file_id: str
    file_path: str
    visible_table_names: list[str]
    caller_user_id: str
    caller_user_email: str | None = None

async def {function_name}(ctx: FunctionContext, data: SaveExpenseInput) -> SaveExpenseResult:
    pod = Pod.from_env()
    tables = pod.tables.list(limit=20)
    visible_table_names = [str(table.name) for table in tables.items]
    record = pod.table("{table_name}").create(
        {{
            "title": data.title,
            "note": data.note,
        }}
    )
    row = record

    path = Path("/tmp/function-note-{suffix}.txt")
    path.write_text(data.note, encoding="utf-8")
    uploaded = pod.files.upload(
        path,
        name="function-note-{suffix}.txt",
        directory_path="{folder_path}",
    )

    return SaveExpenseResult(
        record_id=str(row["id"]),
        file_id=str(uploaded.id),
        file_path=str(uploaded.path),
        visible_table_names=visible_table_names,
        caller_user_id=str(ctx.user_id),
        caller_user_email=ctx.user_email,
    )"""

    function = await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Datastore and file round trip",
            "code": code,
        },
    )

    function_self_grant = {
        "resource_type": "function",
        "resource_name": function["name"],
        "permission_ids": ["function.read"],
    }
    table_and_folder_grants = [
        {
            "resource_type": "datastore_table",
            "resource_name": table["name"],
            "permission_ids": ["datastore.table.read", "datastore.record.write"],
        },
        {
            "resource_type": "folder",
            "resource_name": folder["path"],
            "permission_ids": ["folder.read", "folder.write"],
        },
    ]
    grants = [function_self_grant, *table_and_folder_grants]
    await replace_role_resource_grants(
        authenticated_client,
        pod_id,
        "POD_ADMIN",
        grants,
    )
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [function_self_grant],
    )

    denied_run = await run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"title": "Denied taxi", "note": "no workload grant yet"},
        expected_status="FAILED",
    )
    assert denied_run["error"]

    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        grants,
    )

    final_run = await run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"title": "Taxi", "note": "airport pickup"},
    )
    output = final_run["output_data"]
    assert output["caller_user_id"]
    assert output["caller_user_email"]
    assert table_name in output["visible_table_names"]

    records_response = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/{table_name}/records",
    )
    assert records_response.status_code == status.HTTP_200_OK, records_response.text
    records_payload = records_response.json()
    assert records_payload["total"] == 1
    assert records_payload["items"][0]["id"] == output["record_id"]
    assert records_payload["items"][0]["title"] == "Taxi"
    assert records_payload["items"][0]["note"] == "airport pickup"
    assert records_payload["items"][0]["user_id"] == output["caller_user_id"]

    file_response = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/files/by-path",
        params={"path": output["file_path"]},
    )
    assert file_response.status_code == status.HTTP_200_OK, file_response.text
    assert file_response.json()["name"] == f"function-note-{suffix}.txt"
    assert file_response.json()["path"] == f"{folder_path}/function-note-{suffix}.txt"

    download_response = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/files/download",
        params={"path": output["file_path"]},
    )
    assert download_response.status_code == status.HTTP_200_OK, download_response.text
    assert download_response.text == "airport pickup"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "function_type,enable_rls",
    [
        pytest.param("API", True, id="api-rls"),
        pytest.param("API", False, id="api-no-rls"),
        pytest.param("JOB", True, id="job-rls"),
        pytest.param("JOB", False, id="job-no-rls"),
    ],
)
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_function_record_write_honors_record_grants_for_all_table_types(
    authenticated_client,
    test_pod,
    worker,
    function_type,
    enable_rls,
):
    """A function can write/read records on RLS and non-RLS tables once it holds
    record grants, and is denied with a real 403 when the table is ungranted —
    for both API and JOB functions."""
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"rec_writer_{suffix}"
    table_name = f"sync_runs_{suffix}"

    await create_table(
        authenticated_client,
        pod_id,
        table_name,
        enable_rls=enable_rls,
    )

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Record write grant matrix",
            "type": function_type,
            "code": record_grant_function_code(function_name, table_name),
        },
    )

    function_self_grant = {
        "resource_type": "function",
        "resource_name": function_name,
        "permission_ids": ["function.read"],
    }

    # No table grant at all -> the data call must fail with a real 403.
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [function_self_grant],
    )

    denied_run = await run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"title": "denied", "note": "ungranted table"},
    )
    denied_output = denied_run["output_data"]
    assert denied_output["denied"] is True, denied_output
    assert denied_output["status_code"] == 403, denied_output
    assert denied_output["error_code"] == "MISSING_WORKLOAD_RESOURCE_GRANT", (
        denied_output
    )

    # Grant record read/write (plus table.read for metadata). Notably NOT
    # table.update: data access is governed by record permissions only.
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [
            function_self_grant,
            {
                "resource_type": "datastore_table",
                "resource_name": table_name,
                "permission_ids": [
                    "datastore.table.read",
                    "datastore.record.read",
                    "datastore.record.write",
                ],
            },
        ],
    )

    granted_run = await run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"title": "granted", "note": "ok"},
    )
    output = granted_run["output_data"]
    assert output["denied"] is False, output
    assert output["record_id"], output
    assert output["read_title"] == "granted", output

    records_response = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/{table_name}/records",
    )
    assert records_response.status_code == status.HTTP_200_OK, records_response.text
    payload = records_response.json()
    assert payload["total"] == 1
    assert payload["items"][0]["id"] == output["record_id"]
    assert payload["items"][0]["title"] == "granted"


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_a_function_writes_many_rows_through_the_table_facade(
    authenticated_client,
    test_pod,
    worker,
):
    """``pod.table(...).bulk_*`` reaches the real bulk endpoints and is correct.

    The unit tests pin that each facade method delegates to the right endpoint
    with the table bound and the body intact; they cannot show that the round
    trip works. This runs the real SDK against the real API and Postgres.
    """
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"bulk_writer_{suffix}"
    table_name = f"bulk_rows_{suffix}"
    rows = 50

    await create_table(authenticated_client, pod_id, table_name, enable_rls=False)
    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Batch writes through the bound table helper",
            "type": "API",
            "code": bulk_facade_function_code(function_name, table_name),
        },
    )
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [
            {
                "resource_type": "function",
                "resource_name": function_name,
                "permission_ids": ["function.read"],
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
        ],
    )

    run = await run_function(
        authenticated_client, pod_id, function_name, {"rows": rows}
    )
    output = run["output_data"]

    # Each bulk call reports the rows it affected, not a bare acknowledgement.
    assert output["created"] == rows, output
    assert output["updated"] == 1, output
    assert output["upserted"] == 1, output
    assert output["deleted"] == 2, output
    assert output["retitled"] == "renamed", output

    records_response = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/{table_name}/records",
        params={"limit": rows},
    )
    assert records_response.status_code == status.HTTP_200_OK, records_response.text
    payload = records_response.json()
    # The upsert landed on the existing row rather than inserting a duplicate,
    # so the only rows missing are the two that were deleted.
    assert payload["total"] == rows - 2, payload
    titles = {item["title"] for item in payload["items"]}
    assert "renamed" in titles and "upserted" in titles, titles


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_function_record_write_requires_record_write_not_table_update(
    authenticated_client,
    test_pod,
    worker,
):
    """table.update is schema-only: it does not authorize record writes. Only
    datastore.record.write does — on a non-RLS table where the old code wrongly
    demanded table.update."""
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"rec_writer_perm_{suffix}"
    table_name = f"shared_log_{suffix}"

    await create_table(
        authenticated_client,
        pod_id,
        table_name,
        enable_rls=False,
    )

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "record write requires record.write",
            "type": "JOB",
            "code": record_grant_function_code(function_name, table_name),
        },
    )

    function_self_grant = {
        "resource_type": "function",
        "resource_name": function_name,
        "permission_ids": ["function.read"],
    }

    # Schema permissions only (table.read + table.update), no record.write.
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [
            function_self_grant,
            {
                "resource_type": "datastore_table",
                "resource_name": table_name,
                "permission_ids": [
                    "datastore.table.read",
                    "datastore.table.update",
                ],
            },
        ],
    )

    denied_run = await run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"title": "denied", "note": "schema perms only"},
    )
    denied_output = denied_run["output_data"]
    assert denied_output["denied"] is True, denied_output
    assert denied_output["status_code"] == 403, denied_output
    assert denied_output["error_code"] == "MISSING_WORKLOAD_RESOURCE_GRANT", (
        denied_output
    )

    # Swap table.update for record.write -> the write now succeeds.
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [
            function_self_grant,
            {
                "resource_type": "datastore_table",
                "resource_name": table_name,
                "permission_ids": [
                    "datastore.table.read",
                    "datastore.record.read",
                    "datastore.record.write",
                ],
            },
        ],
    )

    granted_run = await run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"title": "granted", "note": "ok"},
    )
    output = granted_run["output_data"]
    assert output["denied"] is False, output
    assert output["read_title"] == "granted", output


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_api_function_datastore_read_write_latency_sequence(
    authenticated_client,
    test_pod,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    table_name = f"latency_expenses_{suffix}"
    writer_name = f"latency_writer_{suffix}"
    reader_name = f"latency_reader_{suffix}"
    total_hot_runs = 12

    table = await create_table(authenticated_client, pod_id, table_name)

    writer_code = f"""#input_type_name: WriteInput
#output_type_name: WriteResult
#function_name: {writer_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

class WriteInput(BaseModel):
    title: str
    note: str

class WriteResult(BaseModel):
    record_id: str
    title: str
    note: str

async def {writer_name}(ctx: FunctionContext, data: WriteInput) -> WriteResult:
    pod = Pod.from_env()
    record = pod.table("{table_name}").create(
        {{"title": data.title, "note": data.note}}
    )
    row = record
    return WriteResult(
        record_id=str(row["id"]),
        title=str(row["title"]),
        note=str(row["note"]),
    )"""

    reader_code = f"""#input_type_name: ReadInput
#output_type_name: ReadResult
#function_name: {reader_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

class ReadInput(BaseModel):
    record_id: str

class ReadResult(BaseModel):
    record_id: str
    title: str
    note: str | None = None

async def {reader_name}(ctx: FunctionContext, data: ReadInput) -> ReadResult:
    pod = Pod.from_env()
    record = pod.table("{table_name}").get(data.record_id)
    row = record
    return ReadResult(
        record_id=str(row["id"]),
        title=str(row["title"]),
        note=row.get("note"),
    )"""

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": writer_name,
            "description": "Datastore write latency benchmark",
            "type": "API",
            "code": writer_code,
        },
    )
    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": reader_name,
            "description": "Datastore read latency benchmark",
            "type": "API",
            "code": reader_code,
        },
    )
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        writer_name,
        [
            {
                "resource_type": "datastore_table",
                "resource_name": table["name"],
                "permission_ids": [
                    "datastore.table.read",
                    "datastore.record.write",
                ],
            }
        ],
    )
    await replace_function_resource_grants(
        authenticated_client,
        pod_id,
        reader_name,
        [
            {
                "resource_type": "datastore_table",
                "resource_name": table["name"],
                "permission_ids": [
                    "datastore.table.read",
                    "datastore.record.read",
                ],
            }
        ],
    )
    await replace_role_resource_grants(
        authenticated_client,
        pod_id,
        "POD_ADMIN",
        [
            {
                "resource_type": "datastore_table",
                "resource_name": table["name"],
                "permission_ids": [
                    "datastore.table.read",
                    "datastore.record.read",
                    "datastore.record.write",
                ],
            }
        ],
    )

    async def timed_run(function_name: str, input_data: dict) -> tuple[float, dict]:
        started = time.perf_counter()
        final_run = await run_function(
            authenticated_client,
            pod_id,
            function_name,
            input_data,
        )
        return time.perf_counter() - started, final_run

    first_write_elapsed, first_write = await timed_run(
        writer_name,
        {"title": "first", "note": "cold-ish write"},
    )
    first_record_id = first_write["output_data"]["record_id"]
    first_read_elapsed, first_read = await timed_run(
        reader_name,
        {"record_id": first_record_id},
    )
    assert first_read["output_data"]["title"] == "first"

    hot_write_durations: list[float] = []
    hot_read_durations: list[float] = []
    for index in range(total_hot_runs):
        write_elapsed, write_run = await timed_run(
            writer_name,
            {"title": f"hot-{index}", "note": f"note-{index}"},
        )
        hot_write_durations.append(write_elapsed)
        read_elapsed, read_run = await timed_run(
            reader_name,
            {"record_id": write_run["output_data"]["record_id"]},
        )
        hot_read_durations.append(read_elapsed)
        assert read_run["output_data"]["title"] == f"hot-{index}"

    avg_hot_write = sum(hot_write_durations) / len(hot_write_durations)
    avg_hot_read = sum(hot_read_durations) / len(hot_read_durations)
    print(
        "Function datastore sequential latency benchmark: "
        f"hot_runs={total_hot_runs} "
        f"first_write={first_write_elapsed:.3f}s "
        f"avg_write={avg_hot_write:.3f}s "
        f"min_write={min(hot_write_durations):.3f}s "
        f"max_write={max(hot_write_durations):.3f}s "
        f"first_read={first_read_elapsed:.3f}s "
        f"avg_read={avg_hot_read:.3f}s "
        f"min_read={min(hot_read_durations):.3f}s "
        f"max_read={max(hot_read_durations):.3f}s"
    )

    records_response = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/{table_name}/records",
    )
    assert records_response.status_code == status.HTTP_200_OK, records_response.text
    assert records_response.json()["total"] == total_hot_runs + 1


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_function_execute_requires_only_execute_not_read(
    authenticated_client,
    async_client,
    fixed_test_org,
    worker,
):
    """A principal granted only function.execute (no function.read) can run a
    function. Execution must not also require function.read — mirroring
    agent.execute for agent-as-tool, so function/agent tool grants stay minimal.
    """
    ctx = await create_role_visibility_context(
        authenticated_client,
        async_client,
        fixed_test_org,
        pod_name_prefix="function-execute-only",
        custom_role="FUNCTION_EXECUTORS",
    )
    pod_id = ctx["pod_id"]
    name = f"ping_{uuid4().hex[:8]}"
    code = (
        f"#input_type_name: PingInput\n"
        f"#output_type_name: PingResult\n"
        f"#function_name: {name}\n\n"
        "from pydantic import BaseModel\n"
        "from lemma_sdk import FunctionContext, Pod\n\n"
        "class PingInput(BaseModel):\n"
        "    n: int = 1\n\n"
        "class PingResult(BaseModel):\n"
        "    doubled: int\n\n"
        f"async def {name}(ctx: FunctionContext, data: PingInput) -> PingResult:\n"
        "    return PingResult(doubled=data.n * 2)\n"
    )
    function = await create_function(
        authenticated_client,
        pod_id,
        {
            "name": name,
            "description": "execute-only ping",
            "visibility": "RESTRICTED",
            "code": code,
        },
    )

    # Grant the custom role ONLY function.execute on the RESTRICTED function —
    # no function.read. (RESTRICTED means no default visibility, so the role's
    # POD_VIEWER membership grants no read on it either.)
    grant = await authenticated_client.put(
        f"/pods/{pod_id}/roles/{ctx['custom_role']}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "function",
                    "resource_name": function["name"],
                    "permission_ids": ["function.execute"],
                }
            ]
        },
    )
    assert grant.status_code == status.HTTP_200_OK, grant.text

    # The custom-role user runs it. Before the fix this returned 403
    # "Missing permission function.read"; now it must be accepted.
    run_response = await async_client.post(
        f"/pods/{pod_id}/functions/{name}/runs",
        json={"input_data": {"n": 21}},
        headers=ctx["custom_headers"],
        follow_redirects=True,
    )
    assert run_response.status_code == status.HTTP_200_OK, run_response.text

    # Confirm it actually executed (poll as admin, who can read the run).
    final_run = await wait_for_run_completion(
        authenticated_client, pod_id, name, run_response.json()["id"]
    )
    assert final_run["status"] == "COMPLETED", final_run
    assert final_run["output_data"]["doubled"] == 42


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_update_function_replaces_code_and_activates_new_revision(
    authenticated_client, test_pod
):
    """``update_function``'s code path (compile+activate a new immutable
    revision via ``_apply_code``, then persist it) is otherwise untested:
    ``test_function_lifecycle`` only PATCHes ``description``, whose
    ``plan.code`` is ``None`` and never reaches it."""
    pod_id = test_pod["id"]
    func_name = f"update_code_{uuid4().hex[:8]}"

    original_code = typed_function_code(func_name, expression="data.value * 2")
    func = await create_function(
        authenticated_client,
        pod_id,
        {"name": func_name, "description": "before", "code": original_code},
    )
    original_revision = func["revision_hash"]
    assert original_revision and func["status"] == "READY"

    original_result = await run_function(
        authenticated_client, pod_id, func_name, {"value": 10}
    )
    assert original_result["output_data"]["result"] == 20

    updated_code = typed_function_code(func_name, expression="data.value * 3")
    update_response = await authenticated_client.patch(
        f"/pods/{pod_id}/functions/{func_name}",
        json={"code": updated_code},
    )
    assert update_response.status_code == status.HTTP_200_OK, update_response.text
    updated = update_response.json()
    assert updated["status"] == "READY"
    # A different revision was compiled and atomically activated -- not just
    # the same row with a new description.
    assert updated["revision_hash"] != original_revision

    updated_result = await run_function(
        authenticated_client, pod_id, func_name, {"value": 10}
    )
    assert updated_result["output_data"]["result"] == 30


@pytest.mark.asyncio
async def test_update_function_replaces_icon_and_the_change_persists(
    authenticated_client, test_pod
):
    """``update_function``'s icon-cleanup branch (``service.delete_old_icon``,
    called with no pooled connection held once the persist UoW closes) never
    runs unless an update actually changes ``icon_url``; every function in
    this file otherwise either has no icon or leaves it untouched."""
    pod_id = test_pod["id"]
    func_name = f"update_icon_{uuid4().hex[:8]}"

    func = await create_function(
        authenticated_client,
        pod_id,
        {
            "name": func_name,
            "description": "icon test",
            "icon_url": "https://example.test/old-icon.png",
        },
    )
    assert func["icon_url"] == "https://example.test/old-icon.png"

    update_response = await authenticated_client.patch(
        f"/pods/{pod_id}/functions/{func_name}",
        json={"icon_url": "https://example.test/new-icon.png"},
    )
    assert update_response.status_code == status.HTTP_200_OK, update_response.text
    assert update_response.json()["icon_url"] == "https://example.test/new-icon.png"

    # Re-fetch (rather than trust the PATCH echo) to confirm the new URL was
    # actually persisted, not just returned.
    get_response = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{func_name}"
    )
    assert get_response.status_code == status.HTTP_200_OK, get_response.text
    assert get_response.json()["icon_url"] == "https://example.test/new-icon.png"


@pytest.mark.asyncio
async def test_delete_function_cleans_up_icon_and_the_function_becomes_unreachable(
    authenticated_client, test_pod
):
    """``delete_function``'s icon cleanup (``service.delete_icon``, run after
    ``resolve_delete``'s UoW closes) is a no-op whenever the deleted function
    never had an icon -- which is every delete in ``test_function_lifecycle``."""
    pod_id = test_pod["id"]
    func_name = f"delete_icon_{uuid4().hex[:8]}"

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": func_name,
            "description": "to delete",
            "icon_url": "https://example.test/icon.png",
        },
    )

    delete_response = await authenticated_client.delete(
        f"/pods/{pod_id}/functions/{func_name}"
    )
    assert delete_response.status_code == status.HTTP_200_OK, delete_response.text

    get_response = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{func_name}"
    )
    assert get_response.status_code == status.HTTP_404_NOT_FOUND

    run_response = await authenticated_client.post(
        f"/pods/{pod_id}/functions/{func_name}/runs",
        json={"input_data": {}},
        follow_redirects=True,
    )
    assert run_response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_input_that_does_not_match_the_declaration_is_refused_not_run(
    authenticated_client, test_pod
):
    """PS-FUNC-001: refuse before executing, and say which part was wrong.

    The failure mode this replaces is expensive and misleading: the mismatch
    was only found inside the sandbox, so a mistyped field name created a run,
    took a worker lease, usually paid a cold start, and answered 200 with a run
    that had failed. Nothing may be created here.
    """
    pod_id = test_pod["id"]
    func_name = f"strict_input_{uuid4().hex[:8]}"

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": func_name,
            "description": "declares value: int",
            "code": typed_function_code(func_name, expression="data.value * 2"),
        },
    )

    refused = await authenticated_client.post(
        f"/pods/{pod_id}/functions/{func_name}/runs",
        json={"input_data": {"valu": 10}},
        follow_redirects=True,
    )
    assert refused.status_code == status.HTTP_400_BAD_REQUEST, refused.text
    body = refused.json()
    assert body["code"] == "FUNCTION_VALIDATION_ERROR"
    assert "value" in body["message"]

    runs = await authenticated_client.get(f"/pods/{pod_id}/functions/{func_name}/runs")
    assert runs.status_code == status.HTTP_200_OK, runs.text
    assert runs.json()["items"] == [], "a refused call must not create a run"


@pytest.mark.asyncio
@pytest.mark.usefixtures("configure_workspace_api_url")
async def test_deleting_a_function_keeps_the_record_of_what_it_did(
    authenticated_client, test_pod, db_session
):
    """PS-FUNC-004: stop it being runnable, keep the history of what it did.

    ``function_runs.function_id`` used to cascade, so tidying up a pod erased
    every input, output and log the function had ever produced. The rows now
    outlive the definition holding everything they recorded.
    """
    from sqlalchemy import select

    from app.modules.function.infrastructure.models import FunctionRunModel

    pod_id = test_pod["id"]
    func_name = f"delete_history_{uuid4().hex[:8]}"

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": func_name,
            "description": "runs once, then is deleted",
            "code": typed_function_code(func_name, expression="data.value + 1"),
        },
    )
    completed = await run_function(
        authenticated_client, pod_id, func_name, {"value": 41}
    )
    run_id = UUID(completed["id"])

    deleted = await authenticated_client.delete(f"/pods/{pod_id}/functions/{func_name}")
    assert deleted.status_code == status.HTTP_200_OK, deleted.text

    gone = await authenticated_client.get(f"/pods/{pod_id}/functions/{func_name}")
    assert gone.status_code == status.HTTP_404_NOT_FOUND, gone.text

    surviving = (
        await db_session.execute(
            select(FunctionRunModel).where(FunctionRunModel.id == run_id)
        )
    ).scalar_one_or_none()
    assert surviving is not None, "the delete took the run history with it"
    assert surviving.function_id is None, "the run should be detached, not repointed"
    assert surviving.output_data == {"result": 42}
    assert surviving.status == "COMPLETED"


def _revision_code(func_name: str, marker: str, *, extra_field: bool = False) -> str:
    field = "    note: str = ''\n" if extra_field else ""
    return f"""#input_type_name: MarkInput
#output_type_name: MarkResult
#function_name: {func_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class MarkInput(BaseModel):
    text: str
{field}
class MarkResult(BaseModel):
    result: str

async def {func_name}(ctx: FunctionContext, data: MarkInput) -> MarkResult:
    return MarkResult(result="{marker}")"""


@pytest.mark.asyncio
@pytest.mark.usefixtures("sandbox_reachable_backend", "worker")
async def test_function_revision_history_and_rollback(authenticated_client, test_pod):
    """Save code three times, then go back to the first revision.

    The artifacts were always kept -- content-addressed and deleted by nothing --
    so this exercises the index that makes them reachable, and the promotion that
    restores both the code and the contract it implements.
    """
    pod_id = test_pod["id"]
    func_name = f"func_rev_{uuid4().hex[:8]}"

    await create_function(
        authenticated_client,
        pod_id,
        {
            "name": func_name,
            "description": "Revision history e2e",
            "code": _revision_code(func_name, "FIRST"),
        },
    )

    # The second revision changes the input contract; the third does not.
    for marker, extra_field in (("SECOND", True), ("THIRD", True)):
        update_res = await authenticated_client.patch(
            f"/pods/{pod_id}/functions/{func_name}",
            json={"code": _revision_code(func_name, marker, extra_field=extra_field)},
        )
        assert update_res.status_code == status.HTTP_200_OK, update_res.text

    list_res = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{func_name}/revisions"
    )
    assert list_res.status_code == status.HTTP_200_OK, list_res.text
    items = list_res.json()["items"]
    assert [item["revision_number"] for item in items] == [3, 2, 1]  # newest first
    assert [item["is_live"] for item in items] == [True, False, False]
    assert all(item["pruned_at"] is None for item in items)

    live_hash = items[0]["revision_hash"]

    # The single-revision read carries the source and the schemas that revision
    # implements -- the listing deliberately does not pay for either.
    detail_res = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{func_name}/revisions/r1"
    )
    assert detail_res.status_code == status.HTTP_200_OK, detail_res.text
    detail = detail_res.json()
    assert "FIRST" in detail["code"]
    assert "note" not in detail["input_schema"].get("properties", {})

    # A revision is addressable by hash prefix too, which is what a run row has.
    by_hash_res = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{func_name}/revisions/"
        f"{live_hash.removeprefix('sha256:')[:12]}"
    )
    assert by_hash_res.status_code == status.HTTP_200_OK, by_hash_res.text
    assert by_hash_res.json()["revision_number"] == 3

    promote_res = await authenticated_client.post(
        f"/pods/{pod_id}/functions/{func_name}/revisions/r1/promote"
    )
    assert promote_res.status_code == status.HTTP_200_OK, promote_res.text
    promoted = promote_res.json()
    assert promoted["revision"]["revision_number"] == 1
    # r1 predates the added input field, so its contract differs from the live
    # one and callers bound to the newer schema need warning.
    assert promoted["schema_changed"] is True

    after_res = await authenticated_client.get(f"/pods/{pod_id}/functions/{func_name}")
    assert after_res.status_code == status.HTTP_200_OK, after_res.text
    after = after_res.json()
    assert "FIRST" in after["code"]
    # The contract was restored with the code, not left describing r3.
    assert "note" not in after["input_schema"].get("properties", {})

    missing_res = await authenticated_client.post(
        f"/pods/{pod_id}/functions/{func_name}/revisions/r99/promote"
    )
    assert missing_res.status_code == status.HTTP_404_NOT_FOUND, missing_res.text


@pytest.mark.asyncio
@pytest.mark.usefixtures("sandbox_reachable_backend", "worker")
async def test_resaving_identical_code_does_not_mint_a_second_revision(
    authenticated_client, test_pod
):
    """Artifacts are content-addressed, so unchanged code rebuilds to the same
    hash. History must record that as one revision, not one per save."""
    pod_id = test_pod["id"]
    func_name = f"func_same_{uuid4().hex[:8]}"
    code = _revision_code(func_name, "STABLE")

    await create_function(
        authenticated_client,
        pod_id,
        {"name": func_name, "description": "idempotent revision", "code": code},
    )
    resave_res = await authenticated_client.patch(
        f"/pods/{pod_id}/functions/{func_name}", json={"code": code}
    )
    assert resave_res.status_code == status.HTTP_200_OK, resave_res.text

    list_res = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{func_name}/revisions"
    )
    assert [item["revision_number"] for item in list_res.json()["items"]] == [1]
