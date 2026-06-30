from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from typer.testing import CliRunner

BACKEND_DIR = Path(__file__).resolve().parents[5]
REPO_DIR = BACKEND_DIR.parent
LEMMA_PYTHON_DIR = REPO_DIR / "lemma-python"
LEMMA_CLI_DIR = REPO_DIR / "lemma-cli"
for _path in (LEMMA_PYTHON_DIR, LEMMA_CLI_DIR):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

from lemma_cli.cli_core.app import app  # noqa: E402
from lemma_sdk.resources.connectors import BoundConnectors  # noqa: E402


class _RecordingConnectors(BoundConnectors):
    def __init__(self, organization_id: str) -> None:
        super().__init__(None, org_id=organization_id)  # type: ignore[arg-type]
        self.calls: list[dict[str, Any]] = []

    def _call(self, endpoint: Any, *path_args: Any, **kwargs: Any) -> Any:
        self.calls.append(
            {
                "endpoint": getattr(endpoint, "__name__", repr(endpoint)),
                "path_args": path_args,
                "kwargs": kwargs,
            }
        )
        return {"ok": True}


def _openapi_operation_ids() -> set[str]:
    spec_path = LEMMA_PYTHON_DIR / "lemma_sdk" / "openapi_spec.json"
    spec = json.loads(spec_path.read_text(encoding="utf-8"))
    operation_ids: set[str] = set()
    for path_item in spec["paths"].values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            operation_id = operation.get("operationId")
            if operation_id:
                operation_ids.add(operation_id)
    return operation_ids


def test_generated_sdk_spec_includes_connector_lifecycle_operations() -> None:
    operation_ids = _openapi_operation_ids()

    assert {
        "connector.list",
        "connector.operation.execute",
        "connector.connect_request.create",
        "connector.account.list",
        "connector.account.credentials.get",
    }.issubset(operation_ids)


def test_connectors_resource_builds_connect_account_and_execute_calls() -> None:
    organization_id = "019e7f61-8796-72ab-8c0e-4017dd1ad858"
    api = _RecordingConnectors(organization_id)

    api.connect_request("gmail")
    connect_call = api.calls[-1]
    assert connect_call["endpoint"].endswith("connector_connect_request_create")
    # The create endpoint takes only the org in its path
    # (/organizations/{organization_id}/connectors/connect-requests); the
    # connector slug travels in the body as connector_id (see SDK PR #52).
    assert connect_call["path_args"] == (UUID(organization_id),)
    assert connect_call["kwargs"]["body"] == {"connector_id": "gmail"}

    api.accounts.list(app="gmail", limit=25)
    account_list_call = api.calls[-1]
    assert account_list_call["endpoint"].endswith("connector_account_list")
    assert account_list_call["path_args"] == (UUID(organization_id),)
    assert account_list_call["kwargs"]["connector_id"] == "gmail"
    assert account_list_call["kwargs"]["limit"] == 25

    api.execute(
        "workspace-gmail",
        "send_email",
        {"to": "hello@example.com"},
        account_id="acc_123",
    )
    execute_call = api.calls[-1]
    assert execute_call["endpoint"].endswith("connector_operation_execute")
    assert execute_call["path_args"] == (
        UUID(organization_id),
        "workspace-gmail",
        "send_email",
    )
    assert execute_call["kwargs"]["body"] == {
        "payload": {"to": "hello@example.com"},
        "account_id": "acc_123",
    }


def test_cli_exposes_current_connector_command_groups() -> None:
    result = CliRunner().invoke(app, ["connectors", "--help"])

    assert result.exit_code == 0
    assert "connect-requests" in result.output
    assert "operations" in result.output
    assert "accounts" in result.output
