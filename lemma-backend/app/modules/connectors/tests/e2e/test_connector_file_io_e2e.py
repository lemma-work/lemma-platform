"""Files into and out of a connector operation, through the real pod datastore.

The unit tests prove each half against a stand-in for the datastore. What they
cannot prove is the join: that the caller's own pod context is built, that
`/me/...` resolves to *their* folder, that the storage read returns the file
that was uploaded, and that a file result lands back where `output_path` said
-- under `/me/...`, not a raw user id. This runs the REST use case's own file
phases against a real pod, a real upload and the real gateway.
"""

from __future__ import annotations

import base64
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from starlette import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.connectors.api.dependencies import build_connector_operation_use_cases
from app.modules.connectors.api.schemas.connector_operation_schemas import (
    OperationExecutionResponse,
)
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.domain.execution_plan import ResolvedConnectorExecution
from app.modules.connectors.domain.file_input import MaterializedFile

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

PDF = b"%PDF-1.7\n% connector attachment probe\n"

# GMAIL_SEND_EMAIL's `attachment`, as Composio publishes it.
_UPLOADABLE = {
    "type": "object",
    "file_uploadable": True,
    "properties": {"name": {}, "mimetype": {}, "s3key": {}},
}
SEND_SCHEMA = {
    "type": "object",
    "properties": {
        "recipient_email": {"type": "string"},
        "attachment": {"anyOf": [_UPLOADABLE, {"type": "array", "items": _UPLOADABLE}]},
    },
}


def _request() -> SimpleNamespace:
    # What the scopes read off a request: no delegation claims, no cached ctx.
    return SimpleNamespace(state=SimpleNamespace(), headers={})


def _plan(payload: dict[str, object]) -> ResolvedConnectorExecution:
    return ResolvedConnectorExecution(
        connector_id="gmail",
        operation_execution_name="GMAIL_SEND_EMAIL",
        provider="composio",
        third_party_credentials={},
        payload=payload,
        kind="composio",
        input_schema=SEND_SCHEMA,
    )


async def _pod_with_file(client, org) -> str:
    pod = await client.post(
        "/pods",
        json={
            "name": f"connector-files-{uuid4().hex[:8]}",
            "organization_id": org["id"],
            "type": "HYBRID",
        },
    )
    assert pod.status_code == status.HTTP_201_CREATED, pod.text
    pod_id = pod.json()["id"]
    upload = await client.post(
        f"/pods/{pod_id}/datastore/files",
        data={"directory_path": "/me/reports", "search_enabled": "false"},
        files={"data": ("q3.pdf", PDF, "application/pdf")},
    )
    assert upload.status_code == status.HTTP_201_CREATED, upload.text
    return pod_id


async def test_an_attachment_is_read_from_the_callers_own_pod(
    authenticated_client, fixed_test_user, fixed_test_org
):
    pod_id = await _pod_with_file(authenticated_client, fixed_test_org)
    use_cases = build_connector_operation_use_cases(
        SessionUnitOfWorkFactory(async_session_maker)
    )

    prepared = await use_cases._materialize_file_inputs(
        _plan(
            {
                "recipient_email": "ada@example.com",
                "attachment": [{"pod_path": "/me/reports/q3.pdf"}],
                "output_path": "/me/out/receipt.json",
            }
        ),
        user_id=UUID(fixed_test_user["id"]),
        request=_request(),
        pod_id=UUID(pod_id),
    )

    assert prepared.payload["attachment"] == [
        MaterializedFile(PDF, "q3.pdf", "application/pdf")
    ]
    assert prepared.payload["recipient_email"] == "ada@example.com"
    # Lemma's argument, not the provider's.
    assert "output_path" not in prepared.payload
    assert prepared.requested_output_path == "/me/out/receipt.json"


async def test_a_missing_file_is_refused_naming_the_field(
    authenticated_client, fixed_test_user, fixed_test_org
):
    pod_id = await _pod_with_file(authenticated_client, fixed_test_org)
    use_cases = build_connector_operation_use_cases(
        SessionUnitOfWorkFactory(async_session_maker)
    )

    with pytest.raises(ConnectorValidationError) as raised:
        await use_cases._materialize_file_inputs(
            _plan({"attachment": {"pod_path": "/me/reports/missing.pdf"}}),
            user_id=UUID(fixed_test_user["id"]),
            request=_request(),
            pod_id=UUID(pod_id),
        )

    assert raised.value.details["field"] == "$.attachment"


async def test_a_file_result_lands_where_output_path_says(
    authenticated_client, fixed_test_user, fixed_test_org
):
    pod_id = await _pod_with_file(authenticated_client, fixed_test_org)
    use_cases = build_connector_operation_use_cases(
        SessionUnitOfWorkFactory(async_session_maker)
    )
    downloaded = b"id,total\n1,42\n"
    response = OperationExecutionResponse(
        result={
            "data": {
                "type": "binary_content",
                "content_base64": base64.b64encode(downloaded).decode(),
                "media_type": "text/csv",
                "file_name": "export.csv",
            }
        }
    )

    captured = await use_cases._capture_binary_output(
        response,
        output_path="/me/out/export.csv",
        user_id=UUID(fixed_test_user["id"]),
        request=_request(),
        connector_id="gmail",
        pod_id=UUID(pod_id),
    )

    landed = captured.result["data"]
    assert landed["type"] == "pod_file"
    # The path the caller wrote, reusable as the next call's input -- not the
    # raw `/{user-id}/...` storage path.
    assert landed["pod_path"] == "/me/out/export.csv"
    fetched = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/files/download",
        params={"path": "/me/out/export.csv"},
    )
    assert fetched.status_code == status.HTTP_200_OK, fetched.text
    assert fetched.content == downloaded
