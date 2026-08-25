"""Shared helpers for the function module's e2e journeys.

Extracted when `test_function_e2e.py` was split in two. Both halves build
functions, run them, seed connectors and grant resources the same way, so the
helpers had to live somewhere neither half owns.

Here rather than in a sibling `helpers.py`, because
`app/modules/function/tests/e2e/` has no `__init__.py` and the suite runs under
`--import-mode=importlib`, so a sibling module is not reliably importable. This
package already is, and it is where `waiters.py`, `builders.py` and
`e2e_authz.py` live -- the same job.

Names lost their leading underscore on the way in. A `_private` name imported
from another module is a lie about its scope.
"""

from __future__ import annotations

from unittest.mock import patch
from uuid import uuid4

from fastapi import status

from app.modules.connectors.infrastructure.models.account import Account
from app.modules.connectors.infrastructure.models.auth_config import AuthConfig
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.connector_operation import (
    ConnectorOperation,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.test_support.e2e.waiters import wait_for_status


async def wait_for_run_completion(
    authenticated_client,
    pod_id: str,
    function_name: str,
    run_id: str,
    timeout_seconds: int = 60,
):
    async def probe() -> dict:
        res = await authenticated_client.get(
            f"/pods/{pod_id}/functions/{function_name}/runs/{run_id}"
        )
        assert res.status_code == status.HTTP_200_OK, res.text
        return res.json()

    # failed=set(): several callers (e.g. the API-timeout test) legitimately
    # wait FOR a "FAILED" terminus and assert on it themselves afterward --
    # preserve the original loop's behavior of returning on either terminal
    # status rather than fail-fasting on FAILED.
    return await wait_for_status(
        label=f"function {function_name} run {run_id}",
        probe=probe,
        expected={"COMPLETED", "FAILED"},
        failed=set(),
        timeout_seconds=timeout_seconds,
        interval_seconds=0.15,
    )


async def create_function(authenticated_client, pod_id: str, payload: dict) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json=payload,
        follow_redirects=True,
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


def function_payload(name: str, visibility: str | None = None) -> dict:
    payload = {
        "name": name,
        "description": "Function visibility e2e",
    }
    if visibility is not None:
        payload["visibility"] = visibility
    return payload


async def run_function(
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
    final_run = await wait_for_run_completion(
        authenticated_client,
        pod_id,
        function_name,
        run_id,
    )
    assert final_run["status"] == expected_status, {
        "status": final_run["status"],
        "error": final_run.get("error"),
        "run_id": final_run["id"],
    }
    return final_run


async def create_table(
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


async def create_folder(
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


async def replace_role_resource_grants(
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


async def replace_function_resource_grants(
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


async def seed_connector_operation(
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
            kinds=[
                {
                    "kind": "package",
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
        kind="package",
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


async def seed_user(db_session):
    user = User(
        id=uuid4(),
        email=f"function-e2e-{uuid4().hex[:12]}@example.test",
        is_verified=True,
        is_active=True,
    )
    db_session.add(user)
    await db_session.commit()
    return user


def connector_function_code(
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


def patch_connector_operation_execution(connector_id: str):
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


def record_grant_function_code(function_name: str, table_name: str) -> str:
    """Function body that writes a record and reads it back.

    Data-access failures are caught and surfaced as structured output so the
    test can assert on the real HTTP status/code instead of an opaque run
    failure. Reads/writes are gated by record permissions only.
    """
    # Uses typing.Optional on purpose: under Python 3.14 (PEP 649) deferred
    # annotations, schema extraction must resolve typing names from the
    # function's namespace, not just builtins. This guards the sandbox runtime's
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


def bulk_facade_function_code(function_name: str, table_name: str) -> str:
    """A function that writes a realistic number of rows the way one should.

    Every call here goes through ``pod.table(...)``, which until now had no
    batch path at all -- ``list/create/get/update/delete`` and nothing else. A
    caller holding a table handle therefore wrote N rows as N ``create`` calls,
    and from inside a sandbox each one is a full round trip back to the API. A
    benchmark probe did exactly that and spent 17 of its 17.3 seconds on them.
    """
    return f"""#input_type_name: BulkInput
#output_type_name: BulkResult
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

class BulkInput(BaseModel):
    rows: int

class BulkResult(BaseModel):
    created: int
    updated: int
    deleted: int
    upserted: int
    first_id: str
    retitled: str

async def {function_name}(ctx: FunctionContext, data: BulkInput) -> BulkResult:
    pod = Pod.from_env()
    t = pod.table("{table_name}")

    created = t.bulk_create(
        [{{"title": f"row-{{index}}", "note": "seed"}} for index in range(data.rows)]
    )
    rows = t.list(limit=data.rows).to_dict()["items"]
    ids = sorted(str(row["id"]) for row in rows)

    updated = t.bulk_update([{{"id": ids[0], "title": "renamed"}}])
    # An upsert on an existing primary key must update, not fail the request --
    # this is what makes re-seeding idempotent.
    upserted = t.bulk_create([{{"id": ids[1], "title": "upserted"}}], upsert=True)
    deleted = t.bulk_delete(ids[-2:])

    return BulkResult(
        created=created,
        updated=updated,
        deleted=deleted,
        upserted=upserted,
        first_id=ids[0],
        retitled=str(t.get(ids[0])["title"]),
    )"""


def mcp_function_code(function_name: str, auth_config_name: str) -> str:
    return f"""#input_type_name: AddInput
#output_type_name: AddResult
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext, Pod

class AddInput(BaseModel):
    a: int
    b: int

class AddResult(BaseModel):
    total: int

async def {function_name}(ctx: FunctionContext, data: AddInput) -> AddResult:
    pod = Pod.from_env()
    response = pod.connectors.execute(
        "{auth_config_name}",
        "add",
        {{"a": data.a, "b": data.b}},
    )
    result = response.result
    text = str(result)
    digits = "".join(c for c in text if c.isdigit())
    return AddResult(total=int(digits))"""


def typed_function_code(function_name: str, *, expression: str) -> str:
    return f"""#input_type_name: Input
#output_type_name: Output
#function_name: {function_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class Input(BaseModel):
    value: int

class Output(BaseModel):
    result: int

async def {function_name}(ctx: FunctionContext, data: Input) -> Output:
    return Output(result={expression})"""
