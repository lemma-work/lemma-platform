"""End-to-end pod bundle export.

Real ASGI app + the real streaq worker subprocess (from ``test_support`` e2e
fixtures) run the export job for real. The flow: create a pod with a table
(+rows), an agent, and a function; POST an export; poll until READY; download
the archive; extract it with the shared ``lemma_pod_bundle`` library and assert
the bundle layout, resource manifests, and ``data.csv`` seeding.
"""

from __future__ import annotations

import asyncio
import json
from uuid import uuid4

import pytest
from fastapi import status

from lemma_pod_bundle import extract_bundle

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


async def _wait_for_export_ready(
    authenticated_client,
    pod_id: str,
    export_id: str,
    timeout_seconds: int = 60,
) -> dict:
    for _ in range(timeout_seconds):
        res = await authenticated_client.get(
            f"/pods/{pod_id}/bundle/exports/{export_id}"
        )
        assert res.status_code == status.HTTP_200_OK, res.text
        body = res.json()
        if body["status"] in ("READY", "FAILED"):
            return body
        await asyncio.sleep(1)
    raise AssertionError(f"Export did not finish in {timeout_seconds}s")


async def _create_table_with_rows(authenticated_client, pod_id: str, table_name: str) -> None:
    create = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table_name,
            "primary_key_column": "id",
            "enable_rls": True,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
                {"name": "score", "type": "INTEGER", "required": False},
            ],
        },
    )
    assert create.status_code == status.HTTP_201_CREATED, create.text

    for title, score in (("first", 1), ("second", 2)):
        rec = await authenticated_client.post(
            f"/pods/{pod_id}/datastore/tables/{table_name}/records",
            json={"data": {"title": title, "score": score}},
        )
        assert rec.status_code == status.HTTP_201_CREATED, rec.text


async def _create_agent(authenticated_client, pod_id: str, agent_name: str) -> None:
    res = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": agent_name, "instruction": "Answer briefly."},
        follow_redirects=True,
    )
    assert res.status_code == status.HTTP_201_CREATED, res.text


async def _create_function(authenticated_client, pod_id: str, func_name: str) -> None:
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
    res = await authenticated_client.post(
        f"/pods/{pod_id}/functions",
        json={"name": func_name, "description": "export e2e", "code": code},
        follow_redirects=True,
    )
    assert res.status_code == status.HTTP_201_CREATED, res.text


@pytest.mark.workspace
async def test_export_pod_bundle_roundtrip(
    authenticated_client, test_pod, worker, workspace_api, tmp_path
):
    pod_id = test_pod["id"]
    table_name = f"leads_{uuid4().hex[:8]}"
    agent_name = f"assistant_{uuid4().hex[:8]}"
    func_name = f"func_{uuid4().hex[:8]}"

    await _create_table_with_rows(authenticated_client, pod_id, table_name)
    await _create_agent(authenticated_client, pod_id, agent_name)
    await _create_function(authenticated_client, pod_id, func_name)

    # Start the export (202 + export_id).
    start = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/exports",
        json={"with_data": True},
    )
    assert start.status_code == status.HTTP_202_ACCEPTED, start.text
    export_id = start.json()["export_id"]
    assert start.json()["status"] in ("QUEUED", "EXPORTING")

    # Poll until READY.
    final = await _wait_for_export_ready(authenticated_client, pod_id, export_id)
    assert final["status"] == "READY", final
    assert final["bundle_filename"].endswith(".zip")
    assert final["download_url"].endswith(f"/bundle/exports/{export_id}/download")

    # Download + extract.
    download = await authenticated_client.get(
        f"/pods/{pod_id}/bundle/exports/{export_id}/download"
    )
    assert download.status_code == status.HTTP_200_OK, download.text
    assert download.headers["content-type"] == "application/zip"
    assert "attachment" in download.headers["content-disposition"]

    root = extract_bundle(download.content, tmp_path / "bundle")

    # pod.json manifest.
    pod = json.loads((root / "pod.json").read_text())
    assert pod["format_version"] == 2
    assert pod["name"] == test_pod["name"]

    # Table manifest + seeded data.csv (with_data=True).
    table_json = root / "tables" / table_name / f"{table_name}.json"
    assert table_json.is_file()
    assert json.loads(table_json.read_text())["name"] == table_name
    data_csv = root / "tables" / table_name / "data.csv"
    assert data_csv.is_file()
    csv_text = data_csv.read_text()
    assert "title" in csv_text.splitlines()[0]
    assert "first" in csv_text and "second" in csv_text

    # Agent manifest (instruction extracted to a sidecar).
    agent_json = root / "agents" / agent_name / f"{agent_name}.json"
    assert agent_json.is_file()
    agent_payload = json.loads(agent_json.read_text())
    assert agent_payload["instruction"] == {"$file": "instruction.md"}
    assert (root / "agents" / agent_name / "instruction.md").is_file()

    # Function manifest (code extracted to a sidecar).
    func_json = root / "functions" / func_name / f"{func_name}.json"
    assert func_json.is_file()
    func_payload = json.loads(func_json.read_text())
    assert func_payload["code"] == {"$file": "code.py"}
    assert func_name in (root / "functions" / func_name / "code.py").read_text()


async def test_export_status_expired_returns_410(authenticated_client, test_pod, worker):
    pod_id = test_pod["id"]
    missing_export_id = str(uuid4())
    res = await authenticated_client.get(
        f"/pods/{pod_id}/bundle/exports/{missing_export_id}"
    )
    assert res.status_code == status.HTTP_410_GONE, res.text
    assert res.json()["code"] == "POD_BUNDLE_EXPIRED"


async def test_export_without_data_omits_data_csv(authenticated_client, test_pod, worker, tmp_path):
    pod_id = test_pod["id"]
    table_name = f"nodata_{uuid4().hex[:8]}"
    await _create_table_with_rows(authenticated_client, pod_id, table_name)

    start = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/exports",
        json={"with_data": False, "include": ["tables"]},
    )
    assert start.status_code == status.HTTP_202_ACCEPTED, start.text
    export_id = start.json()["export_id"]

    final = await _wait_for_export_ready(authenticated_client, pod_id, export_id)
    assert final["status"] == "READY", final

    download = await authenticated_client.get(
        f"/pods/{pod_id}/bundle/exports/{export_id}/download"
    )
    root = extract_bundle(download.content, tmp_path / "bundle")
    assert (root / "tables" / table_name / f"{table_name}.json").is_file()
    assert not (root / "tables" / table_name / "data.csv").exists()
