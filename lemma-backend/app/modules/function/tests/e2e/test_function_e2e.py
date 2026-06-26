from __future__ import annotations

import asyncio
import time
from unittest.mock import patch
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.connector_operation import (
    ConnectorOperation,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.test_support.e2e_authz import (
    create_role_visibility_context,
    item_names,
)

pytestmark = [pytest.mark.e2e, pytest.mark.workspace]


async def _wait_for_run_completion(
    authenticated_client,
    pod_id: str,
    function_name: str,
    run_id: str,
    timeout_seconds: int = 60,
):
    for _ in range(timeout_seconds):
        res = await authenticated_client.get(
            f"/pods/{pod_id}/functions/{function_name}/runs/{run_id}"
        )
        assert res.status_code == status.HTTP_200_OK, res.text
        run_data = res.json()
        if run_data["status"] in ["COMPLETED", "FAILED"]:
            return run_data
        await asyncio.sleep(1)
    raise AssertionError("Function execution timed out")


async def _create_function(authenticated_client, pod_id: str, payload: dict) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json=payload,
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


def _function_payload(name: str, visibility: str | None = None) -> dict:
    payload = {
        "name": name,
        "description": "Function visibility e2e",
    }
    if visibility is not None:
        payload["visibility"] = visibility
    return payload


@pytest.mark.asyncio
async def test_create_function_rejects_duplicate_name_in_same_pod(
    authenticated_client,
    test_pod,
):
    pod_id = test_pod["id"]
    function_name = f"duplicate_function_{uuid4().hex[:8]}"

    first = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json=_function_payload(function_name),
        follow_redirects=True,
    )
    assert first.status_code == status.HTTP_201_CREATED, first.text

    second = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json=_function_payload(function_name),
        follow_redirects=True,
    )
    assert second.status_code == status.HTTP_409_CONFLICT, second.text
    assert second.json()["code"] == "FUNCTION_CONFLICT"


async def _run_function(
    authenticated_client,
    pod_id: str,
    function_name: str,
    input_data: dict,
    *,
    expected_status: str = "COMPLETED",
) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/functions/{function_name}/runs",
        json={"input_data": input_data},
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    run_id = response.json()["id"]
    final_run = await _wait_for_run_completion(
        authenticated_client,
        pod_id,
        function_name,
        run_id,
    )
    assert final_run["status"] == expected_status, final_run
    return final_run


async def _create_table(
    authenticated_client,
    pod_id: str,
    table_name: str,
    *,
    visibility: str | None = None,
    enable_rls: bool = True,
) -> dict:
    payload = {
        "name": table_name,
        "primary_key_column": "id",
        "enable_rls": enable_rls,
        "columns": [
            {"name": "id", "type": "UUID", "required": True, "auto": True},
            {"name": "title", "type": "TEXT", "required": True},
            {"name": "note", "type": "TEXT", "required": False},
        ],
    }
    if visibility is not None:
        payload["visibility"] = visibility
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json=payload,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _create_folder(
    authenticated_client,
    pod_id: str,
    path: str,
    *,
    visibility: str | None = None,
) -> dict:
    payload = {"path": path}
    if visibility is not None:
        payload["visibility"] = visibility
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/files/folders",
        json=payload,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _replace_role_resource_grants(
    authenticated_client,
    pod_id: str,
    role_name: str,
    grants: list[dict],
) -> dict:
    response = await authenticated_client.put(
        f"/pods/{pod_id}/roles/{role_name}/permissions",
        json={"grants": grants},
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


async def _replace_function_resource_grants(
    authenticated_client,
    pod_id: str,
    function_name: str,
    grants: list[dict],
) -> dict:
    response = await authenticated_client.put(
        f"/pods/{pod_id}/functions/{function_name}/permissions",
        json={"grants": grants},
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()


async def _seed_connector_operation(
    db_session,
    *,
    connector_id: str,
    organization_id: str,
    user_id=None,
    api_key: str | None = None,
):
    app = await db_session.get(Connector, connector_id)
    if app is None:
        app = Connector(
            id=connector_id,
            title=f"{connector_id} title",
            description="Mock app for function e2e",
            provider_capabilities=[
                {
                    "provider": "LEMMA",
                    "auth_scheme": "API_KEY",
                    "system_default_available": True,
                }
            ],
            is_active=True,
        )
        db_session.add(app)

    auth_config = AuthConfig(
        id=uuid4(),
        organization_id=organization_id,
        connector_id=connector_id,
        name=connector_id,
        provider="LEMMA",
        config_source="SYSTEM_DEFAULT",
        status="ACTIVE",
    )
    db_session.add(auth_config)

    operation = await db_session.get(
        ConnectorOperation,
        f"{connector_id}:send_payload",
    )
    if operation is None:
        db_session.add(
            ConnectorOperation(
                id=f"{connector_id}:send_payload",
                connector_id=connector_id,
                name="send_payload",
                provider_operation_name="send_payload",
                display_name="Send Payload",
                description="Mock send payload operation",
                input_schema={
                    "type": "object",
                    "properties": {
                        "message": {"type": "string"},
                        "caller_user_id": {"type": "string"},
                    },
                    "required": ["message", "caller_user_id"],
                },
                output_schema={
                    "type": "object",
                    "properties": {
                        "echoed_message": {"type": "string"},
                        "used_api_key": {"type": "string"},
                    },
                    "required": ["echoed_message", "used_api_key"],
                },
            )
        )

    account = None
    if user_id is not None:
        account = Account(
            id=uuid4(),
            connector_id=connector_id,
            organization_id=organization_id,
            auth_config_id=auth_config.id,
            user_id=user_id,
            credentials={"api_key": api_key},
        )
        db_session.add(account)
    await db_session.commit()
    return account


async def _seed_user(db_session):
    user = User(
        id=uuid4(),
        email=f"function-e2e-{uuid4().hex[:12]}@example.test",
        is_verified=True,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


def _connector_function_code(
    function_name: str,
    connector_id: str,
    *,
    account_id: str | None = None,
) -> str:
    account_id_argument = f',\n        account_id="{account_id}"' if account_id else ""
    return f"""#input_type_name: SendPayloadInput
#output_type_name: SendPayloadResult
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

class SendPayloadInput(BaseModel):
    message: str

class SendPayloadResult(BaseModel):
    echoed_message: str
    used_api_key: str
    caller_user_id: str

async def {function_name}(ctx: FunctionContext, data: SendPayloadInput) -> SendPayloadResult:
    pod = Pod.from_env()
    response = pod.connectors.execute(
        "{connector_id}",
        "send_payload",
        {{
            "message": data.message,
            "caller_user_id": str(ctx.user_id),
        }}{account_id_argument},
    )
    result = response.result
    return SendPayloadResult(
        echoed_message=result["echoed_message"],
        used_api_key=result["used_api_key"],
        caller_user_id=result["caller_user_id"],
    )"""


def _patch_connector_operation_execution(connector_id: str):
    expected_connector_id = connector_id

    async def fake_execute_operation(
        _self,
        connector_id,
        operation_name,
        payload,
        third_party_credentials,
        auth_token=None,
        api_url=None,
    ):
        del auth_token, api_url
        assert connector_id == expected_connector_id
        assert operation_name == "send_payload"
        return {
            "echoed_message": payload["message"],
            "used_api_key": third_party_credentials["api_key"],
            "caller_user_id": payload["caller_user_id"],
        }

    return patch(
        "app.modules.connectors.infrastructure.adapters.lemma_operation_gateway."
        "LemmaOperationGateway.execute_operation",
        new=fake_execute_operation,
    )


@pytest.mark.asyncio
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

    func = await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": func_name,
            "description": "Function CRUD smoke test",
            "code": code,
        },
    )
    assert func["name"] == func_name

    get_response = await authenticated_client.get(f"/pods/{pod_id}/functions/{func_name}")
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

    await _create_function(authenticated_client, pod_id, _function_payload(default_name))
    await _create_function(
        authenticated_client,
        pod_id,
        _function_payload(editor_name, "RESTRICTED"),
    )
    await _create_function(
        authenticated_client,
        pod_id,
        _function_payload(custom_name, "RESTRICTED"),
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

    table = await _create_table(
        authenticated_client,
        pod_id,
        table_name,
        visibility="RESTRICTED",
    )
    folder = await _create_folder(
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

    path = Path("/workspace/function-note-{suffix}.txt")
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

    function = await _create_function(
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
    await _replace_role_resource_grants(
        authenticated_client,
        pod_id,
        "POD_ADMIN",
        grants,
    )
    await _replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [function_self_grant],
    )

    denied_run = await _run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"title": "Denied taxi", "note": "no workload grant yet"},
        expected_status="FAILED",
    )
    assert denied_run["error"]

    await _replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        grants,
    )

    final_run = await _run_function(
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


def _record_grant_function_code(function_name: str, table_name: str) -> str:
    """Function body that writes a record and reads it back.

    Data-access failures are caught and surfaced as structured output so the
    test can assert on the real HTTP status/code instead of an opaque run
    failure. Reads/writes are gated by record permissions only.
    """
    # Uses typing.Optional on purpose: under Python 3.14 (PEP 649) deferred
    # annotations, schema extraction must resolve typing names from the
    # function's namespace, not just builtins. This guards the agentbox runtime
    # fix that registers the execution namespace as a real module.
    return f"""#input_type_name: WriteInput
#output_type_name: WriteResult
#function_name: {function_name}

from typing import Optional
from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod
from lemma_sdk.errors import LemmaAPIError

class WriteInput(BaseModel):
    title: str
    note: str

class WriteResult(BaseModel):
    denied: bool
    status_code: Optional[int] = None
    error_code: Optional[str] = None
    record_id: Optional[str] = None
    read_title: Optional[str] = None

async def {function_name}(ctx: FunctionContext, data: WriteInput) -> WriteResult:
    pod = Pod.from_env()
    try:
        record = pod.table("{table_name}").create(
            {{"title": data.title, "note": data.note}}
        )
    except LemmaAPIError as exc:
        return WriteResult(
            denied=True,
            status_code=exc.status_code,
            error_code=exc.code,
        )
    row = record
    fetched = pod.table("{table_name}").get(str(row["id"]))
    read_row = fetched
    return WriteResult(
        denied=False,
        record_id=str(row["id"]),
        read_title=str(read_row["title"]),
    )"""


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

    await _create_table(
        authenticated_client,
        pod_id,
        table_name,
        enable_rls=enable_rls,
    )

    await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Record write grant matrix",
            "type": function_type,
            "code": _record_grant_function_code(function_name, table_name),
        },
    )

    function_self_grant = {
        "resource_type": "function",
        "resource_name": function_name,
        "permission_ids": ["function.read"],
    }

    # No table grant at all -> the data call must fail with a real 403.
    await _replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [function_self_grant],
    )

    denied_run = await _run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"title": "denied", "note": "ungranted table"},
    )
    denied_output = denied_run["output_data"]
    assert denied_output["denied"] is True, denied_output
    assert denied_output["status_code"] == 403, denied_output
    assert denied_output["error_code"] == "MISSING_WORKLOAD_RESOURCE_GRANT", denied_output

    # Grant record read/write (plus table.read for metadata). Notably NOT
    # table.update: data access is governed by record permissions only.
    await _replace_function_resource_grants(
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

    granted_run = await _run_function(
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

    await _create_table(
        authenticated_client,
        pod_id,
        table_name,
        enable_rls=False,
    )

    await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "record write requires record.write",
            "type": "JOB",
            "code": _record_grant_function_code(function_name, table_name),
        },
    )

    function_self_grant = {
        "resource_type": "function",
        "resource_name": function_name,
        "permission_ids": ["function.read"],
    }

    # Schema permissions only (table.read + table.update), no record.write.
    await _replace_function_resource_grants(
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

    denied_run = await _run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"title": "denied", "note": "schema perms only"},
    )
    denied_output = denied_run["output_data"]
    assert denied_output["denied"] is True, denied_output
    assert denied_output["status_code"] == 403, denied_output
    assert denied_output["error_code"] == "MISSING_WORKLOAD_RESOURCE_GRANT", denied_output

    # Swap table.update for record.write -> the write now succeeds.
    await _replace_function_resource_grants(
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

    granted_run = await _run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"title": "granted", "note": "ok"},
    )
    output = granted_run["output_data"]
    assert output["denied"] is False, output
    assert output["read_title"] == "granted", output


@pytest.mark.asyncio
async def test_api_function_concurrent_hot_runs_reports_average_execution_time(
    authenticated_client,
    test_pod,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"hot_api_{suffix}"
    total_runs = 20
    concurrency = 5

    code = f"""#input_type_name: HotInput
#output_type_name: HotResult
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class HotInput(BaseModel):
    value: int

class HotResult(BaseModel):
    value: int
    doubled: int
    caller_user_id: str

async def {function_name}(ctx: FunctionContext, data: HotInput) -> HotResult:
    return HotResult(
        value=data.value,
        doubled=data.value * 2,
        caller_user_id=str(ctx.user_id),
    )"""

    await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Hot API concurrency and latency smoke test",
            "type": "API",
            "code": code,
        },
    )

    warm_run = await _run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"value": -1},
    )
    assert warm_run["output_data"]["doubled"] == -2

    semaphore = asyncio.Semaphore(concurrency)

    async def run_one(index: int) -> tuple[int, float, dict]:
        async with semaphore:
            started = time.perf_counter()
            final_run = await _run_function(
                authenticated_client,
                pod_id,
                function_name,
                {"value": index},
            )
            elapsed = time.perf_counter() - started
            return index, elapsed, final_run

    wall_started = time.perf_counter()
    results = await asyncio.gather(*(run_one(index) for index in range(total_runs)))
    wall_elapsed = time.perf_counter() - wall_started

    durations = [elapsed for _, elapsed, _ in results]
    average_elapsed = sum(durations) / len(durations)
    print(
        "Function API hot concurrency benchmark: "
        f"runs={total_runs} concurrency={concurrency} "
        f"avg={average_elapsed:.3f}s wall={wall_elapsed:.3f}s "
        f"min={min(durations):.3f}s max={max(durations):.3f}s"
    )

    for index, _elapsed, final_run in results:
        output = final_run["output_data"]
        assert output["value"] == index
        assert output["doubled"] == index * 2
        assert output["caller_user_id"]


@pytest.mark.asyncio
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

    table = await _create_table(authenticated_client, pod_id, table_name)

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

    await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": writer_name,
            "description": "Datastore write latency benchmark",
            "type": "API",
            "code": writer_code,
        },
    )
    await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": reader_name,
            "description": "Datastore read latency benchmark",
            "type": "API",
            "code": reader_code,
        },
    )
    await _replace_function_resource_grants(
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
    await _replace_function_resource_grants(
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
    await _replace_role_resource_grants(
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
        final_run = await _run_function(
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
async def test_function_connector_operation_resolves_user_owned_account_in_backend(
    authenticated_client,
    test_pod,
    fixed_test_user,
    db_session,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    connector_id = f"dynamic_app_{suffix}"
    function_name = f"dynamic_app_func_{suffix}"
    await _seed_connector_operation(
        db_session,
        connector_id=connector_id,
        organization_id=test_pod["organization_id"],
        user_id=fixed_test_user["id"],
        api_key="dynamic-secret",
    )

    function = await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Function app operation using dynamic account resolution",
            "code": _connector_function_code(
                function_name,
                connector_id,
            ),
        },
    )
    await _replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
        ],
    )
    await _replace_role_resource_grants(
        authenticated_client,
        pod_id,
        "POD_ADMIN",
        [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
        ],
    )

    with _patch_connector_operation_execution(connector_id):
        final_run = await _run_function(
            authenticated_client,
            pod_id,
            function_name,
            {"message": "hello-dynamic"},
        )

    output = final_run["output_data"]
    assert output["echoed_message"] == "hello-dynamic"
    assert output["used_api_key"] == "dynamic-secret"
    assert output["caller_user_id"] == str(fixed_test_user["id"])


@pytest.mark.asyncio
async def test_function_connector_operation_resolves_agent_owned_account_in_backend(
    authenticated_client,
    test_pod,
    fixed_test_user,
    db_session,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    connector_id = f"fixed_app_{suffix}"
    function_name = f"fixed_app_func_{suffix}"
    fixed_account_owner = await _seed_user(db_session)
    account = await _seed_connector_operation(
        db_session,
        connector_id=connector_id,
        organization_id=test_pod["organization_id"],
        user_id=fixed_account_owner.id,
        api_key="fixed-secret",
    )

    function = await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Function app operation using fixed account resolution",
            "code": _connector_function_code(
                function_name,
                connector_id,
                account_id=str(account.id),
            ),
        },
    )
    await _replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
            {
                "resource_type": "connector_account",
                "resource_name": str(account.id),
                "permission_ids": ["connector_account.use"],
            },
        ],
    )
    await _replace_role_resource_grants(
        authenticated_client,
        pod_id,
        "POD_ADMIN",
        [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
            {
                "resource_type": "connector_account",
                "resource_name": str(account.id),
                "permission_ids": ["connector_account.use"],
            },
        ],
    )

    with _patch_connector_operation_execution(connector_id):
        final_run = await _run_function(
            authenticated_client,
            pod_id,
            function_name,
            {"message": "hello-fixed"},
        )

    output = final_run["output_data"]
    assert output["echoed_message"] == "hello-fixed"
    assert output["used_api_key"] == "fixed-secret"
    assert output["caller_user_id"] == str(fixed_test_user["id"])


@pytest.mark.asyncio
async def test_function_connector_operation_fails_when_user_owned_account_missing(
    authenticated_client,
    test_pod,
    fixed_test_user,
    db_session,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    connector_id = f"missing_account_app_{suffix}"
    function_name = f"missing_account_func_{suffix}"
    await _seed_connector_operation(
        db_session,
        connector_id=connector_id,
        organization_id=test_pod["organization_id"],
    )

    function = await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Function app operation missing user account",
            "code": _connector_function_code(
                function_name,
                connector_id,
            ),
        },
    )
    await _replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
        [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            }
        ],
    )
    await _replace_role_resource_grants(
        authenticated_client,
        pod_id,
        "POD_ADMIN",
        [
            {
                "resource_type": "function",
                "resource_name": function["name"],
                "permission_ids": ["function.read"],
            },
            {
                "resource_type": "connector",
                "resource_name": connector_id,
                "permission_ids": ["connector.use"],
            },
        ],
    )

    final_run = await _run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"message": "hello-missing"},
        expected_status="FAILED",
    )

    assert final_run["error"]
    assert "ACCOUNT_RESOLUTION_ERROR" in final_run["error"]
    assert "Connect your account first" in final_run["error"]


@pytest.mark.asyncio
async def test_api_function_timeout_marks_run_failed_and_stops_execution(
    authenticated_client,
    test_pod,
    monkeypatch,
):
    from app.modules.function.application import (
        function_run_executor as function_engine_module,
    )

    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"api_timeout_{suffix}"
    table_name = f"timeout_records_{suffix}"

    await _create_table(authenticated_client, pod_id, table_name)
    monkeypatch.setattr(function_engine_module, "_API_FUNCTION_TIMEOUT_SECONDS", 2)

    code = f"""#input_type_name: TimeoutInput
#output_type_name: TimeoutResult
#function_name: {function_name}

import asyncio
from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

class TimeoutInput(BaseModel):
    title: str

class TimeoutResult(BaseModel):
    record_id: str

async def {function_name}(ctx: FunctionContext, data: TimeoutInput) -> TimeoutResult:
    await asyncio.sleep(5)
    pod = Pod.from_env()
    record = pod.table("{table_name}").create(
        {{
            "title": data.title,
        }}
    )
    row = record
    return TimeoutResult(record_id=str(row["id"]))"""

    await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "API function timeout smoke test",
            "type": "API",
            "code": code,
        },
    )

    response = await authenticated_client.post(
        f"/pods/{pod_id}/functions/{function_name}/runs",
        json={"input_data": {"title": "should-not-write"}},
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    run_id = response.json()["id"]

    final_run = await _wait_for_run_completion(
        authenticated_client,
        pod_id,
        function_name,
        run_id,
        timeout_seconds=15,
    )
    assert final_run["status"] == "FAILED", final_run
    assert final_run["error"]
    assert "timed out" in final_run["error"].lower()

    await asyncio.sleep(4)

    rerun_response = await authenticated_client.get(
        f"/pods/{pod_id}/functions/{function_name}/runs/{run_id}"
    )
    assert rerun_response.status_code == status.HTTP_200_OK, rerun_response.text
    assert rerun_response.json()["status"] == "FAILED"

    records_response = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/tables/{table_name}/records",
    )
    assert records_response.status_code == status.HTTP_200_OK, records_response.text
    assert records_response.json()["total"] == 0


@pytest.mark.asyncio
async def test_job_function_run_completes_via_worker(
    authenticated_client,
    test_pod,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"job_func_{suffix}"

    code = f"""#input_type_name: JobInput
#output_type_name: JobResult
#function_name: {function_name}

import asyncio
from pydantic import BaseModel
from lemma_sdk import FunctionContext

class JobInput(BaseModel):
    text: str

class JobResult(BaseModel):
    result: str

async def {function_name}(ctx: FunctionContext, data: JobInput) -> JobResult:
    await asyncio.sleep(1)
    return JobResult(result=data.text.upper())"""

    await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Queued function execution smoke test",
            "type": "JOB",
            "code": code,
        },
    )

    response = await authenticated_client.post(
        f"/pods/{pod_id}/functions/{function_name}/runs",
        json={"input_data": {"text": "queued hello"}},
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    run = response.json()
    assert run["status"] in {"PENDING", "RUNNING"}
    assert run["job_id"]

    final_run = await _wait_for_run_completion(
        authenticated_client,
        pod_id,
        function_name,
        run["id"],
        timeout_seconds=30,
    )
    assert final_run["status"] == "COMPLETED", final_run
    assert final_run["output_data"]["result"] == "QUEUED HELLO"


@pytest.mark.asyncio
async def test_job_function_execution_writes_datastore_record(
    authenticated_client,
    test_pod,
    worker,
):
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"job_store_{suffix}"
    table_name = f"expenses_{suffix}"

    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table_name,
            "primary_key_column": "id",
            "enable_rls": True,
            "columns": [
                {"name": "title", "type": "TEXT", "required": True},
                {"name": "note", "type": "TEXT", "required": False},
            ],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    table = response.json()

    code = f"""#input_type_name: SaveExpenseInput
#output_type_name: SaveExpenseResult
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

class SaveExpenseInput(BaseModel):
    title: str
    note: str

class SaveExpenseResult(BaseModel):
    record_id: str
    caller_user_id: str
    caller_user_email: str | None = None

async def {function_name}(ctx: FunctionContext, data: SaveExpenseInput) -> SaveExpenseResult:
    pod = Pod.from_env()
    record = pod.table("{table_name}").create(
        {{
            "title": data.title,
            "note": data.note,
        }}
    )
    row = record
    return SaveExpenseResult(
        record_id=str(row["id"]),
        caller_user_id=str(ctx.user_id),
        caller_user_email=ctx.user_email,
    )"""

    await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "Queued datastore write smoke test",
            "type": "JOB",
            "code": code,
        },
    )
    await _replace_function_resource_grants(
        authenticated_client,
        pod_id,
        function_name,
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
    await _replace_role_resource_grants(
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

    final_run = await _run_function(
        authenticated_client,
        pod_id,
        function_name,
        {"title": "Taxi", "note": "airport pickup"},
    )
    assert final_run["status"] == "COMPLETED", final_run
    output = final_run["output_data"]
    assert output["caller_user_id"]

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


@pytest.mark.slow
@pytest.mark.asyncio
async def test_job_function_long_run_survives_sandbox_idle_reaper(
    authenticated_client,
    test_pod,
    worker,
):
    """A JOB that runs longer than the sandbox idle timeout still completes.

    A JOB occupies the sandbox through the function_executor app and holds no
    runtime session, so without the worker's keepalive heartbeat the idle reaper
    would delete the sandbox mid-run. Here the sandbox idle window is shrunk well
    below the function's ~60s runtime, so the run only completes if the heartbeat
    keeps resetting the idle clock. (The worker heartbeats every 30s by default,
    so the idle timeout must stay above that.)
    """
    import agentbox.config as agentbox_config

    original = {
        "sandbox_idle": agentbox_config.settings.agentbox_sandbox_idle_timeout_seconds,
        "session_idle": agentbox_config.settings.agentbox_session_idle_timeout_seconds,
        "cleanup": agentbox_config.settings.agentbox_cleanup_interval_seconds,
    }
    agentbox_config.settings.agentbox_sandbox_idle_timeout_seconds = 45
    agentbox_config.settings.agentbox_session_idle_timeout_seconds = 45
    agentbox_config.settings.agentbox_cleanup_interval_seconds = 5
    try:
        pod_id = test_pod["id"]
        suffix = uuid4().hex[:8]
        function_name = f"job_long_{suffix}"
        code = f"""#input_type_name: JobInput
#output_type_name: JobResult
#function_name: {function_name}

import asyncio
from pydantic import BaseModel
from lemma_sdk import FunctionContext

class JobInput(BaseModel):
    seconds: int

class JobResult(BaseModel):
    slept: int

async def {function_name}(ctx: FunctionContext, data: JobInput) -> JobResult:
    await asyncio.sleep(data.seconds)
    return JobResult(slept=data.seconds)"""

        await _create_function(
            authenticated_client,
            pod_id,
            {
                "name": function_name,
                "description": "Long-running job: sandbox keepalive smoke test",
                "type": "JOB",
                "code": code,
            },
        )

        response = await authenticated_client.post(
            f"/pods/{pod_id}/functions/{function_name}/runs",
            json={"input_data": {"seconds": 60}},
            follow_redirects=True,
        )
        assert response.status_code == status.HTTP_200_OK, response.text
        run = response.json()

        final_run = await _wait_for_run_completion(
            authenticated_client,
            pod_id,
            function_name,
            run["id"],
            timeout_seconds=150,
        )
        assert final_run["status"] == "COMPLETED", final_run
        assert final_run["output_data"]["slept"] == 60
    finally:
        agentbox_config.settings.agentbox_sandbox_idle_timeout_seconds = original[
            "sandbox_idle"
        ]
        agentbox_config.settings.agentbox_session_idle_timeout_seconds = original[
            "session_idle"
        ]
        agentbox_config.settings.agentbox_cleanup_interval_seconds = original["cleanup"]


@pytest.mark.asyncio
async def test_concurrent_api_function_runs_all_complete(
    authenticated_client,
    test_pod,
):
    """3-4 API runs fired together (same user -> shared sandbox) all complete.

    Concurrent cold-start callers must coordinate on one sandbox creation
    (Redis creation lock) and then share the RUNNING sandbox; none should fail
    with a sandbox readiness / "Sandbox not found" race.
    """
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"api_concurrent_{suffix}"
    code = f"""#input_type_name: ConcInput
#output_type_name: ConcResult
#function_name: {function_name}

import asyncio
from pydantic import BaseModel
from lemma_sdk import FunctionContext

class ConcInput(BaseModel):
    n: int

class ConcResult(BaseModel):
    doubled: int

async def {function_name}(ctx: FunctionContext, data: ConcInput) -> ConcResult:
    await asyncio.sleep(2)
    return ConcResult(doubled=data.n * 2)"""

    await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "concurrency smoke (API)",
            "type": "API",
            "code": code,
        },
    )

    async def run_one(n: int) -> dict:
        return await _run_function(
            authenticated_client, pod_id, function_name, {"n": n}
        )

    results = await asyncio.gather(*(run_one(n) for n in range(1, 5)))
    for n, final in zip(range(1, 5), results):
        assert final["status"] == "COMPLETED", final
        assert final["output_data"]["doubled"] == n * 2


@pytest.mark.asyncio
async def test_concurrent_job_function_runs_all_complete(
    authenticated_client,
    test_pod,
    worker,
):
    """3-4 JOB runs fired together (same user -> shared sandbox) all complete."""
    pod_id = test_pod["id"]
    suffix = uuid4().hex[:8]
    function_name = f"job_concurrent_{suffix}"
    code = f"""#input_type_name: ConcInput
#output_type_name: ConcResult
#function_name: {function_name}

import asyncio
from pydantic import BaseModel
from lemma_sdk import FunctionContext

class ConcInput(BaseModel):
    n: int

class ConcResult(BaseModel):
    doubled: int

async def {function_name}(ctx: FunctionContext, data: ConcInput) -> ConcResult:
    await asyncio.sleep(3)
    return ConcResult(doubled=data.n * 2)"""

    await _create_function(
        authenticated_client,
        pod_id,
        {
            "name": function_name,
            "description": "concurrency smoke (JOB)",
            "type": "JOB",
            "code": code,
        },
    )

    async def trigger(n: int) -> str:
        resp = await authenticated_client.post(
            f"/pods/{pod_id}/functions/{function_name}/runs",
            json={"input_data": {"n": n}},
            follow_redirects=True,
        )
        assert resp.status_code == status.HTTP_200_OK, resp.text
        return resp.json()["id"]

    run_ids = await asyncio.gather(*(trigger(n) for n in range(1, 5)))
    finals = await asyncio.gather(
        *(
            _wait_for_run_completion(
                authenticated_client, pod_id, function_name, rid, timeout_seconds=150
            )
            for rid in run_ids
        )
    )
    for final in finals:
        assert final["status"] == "COMPLETED", final


@pytest.mark.asyncio
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
    function = await _create_function(
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
    final_run = await _wait_for_run_completion(
        authenticated_client, pod_id, name, run_response.json()["id"]
    )
    assert final_run["status"] == "COMPLETED", final_run
    assert final_run["output_data"]["doubled"] == 42
