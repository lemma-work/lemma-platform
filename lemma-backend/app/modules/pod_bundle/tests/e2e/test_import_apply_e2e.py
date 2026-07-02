"""End-to-end pod bundle import apply.

Real ASGI app + the real streaq worker run plan then apply. Most tests build the
bundle in-process (``worker`` marker, no sandbox); the final roundtrip creates a
real function via the agentbox (``workspace`` marker) to prove export→import→apply
across process boundaries.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import status

from lemma_pod_bundle import pack_bundle

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


def _write(path: Path, payload: dict | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(payload, str):
        path.write_text(payload, encoding="utf-8")
    else:
        path.write_text(json.dumps(payload), encoding="utf-8")


def _make_bundle(
    tmp_path: Path,
    *,
    name: str = "Imported CRM",
    table: tuple[str, list[dict]] | None = None,
    rows_csv: str | None = None,
    agents: list[str] | None = None,
) -> bytes:
    root = tmp_path / f"bundle-{uuid4().hex[:8]}"
    _write(root / "pod.json", {"name": name, "format_version": 2, "variables": {}})
    if table is not None:
        tname, columns = table
        _write(
            root / "tables" / tname / f"{tname}.json",
            {"name": tname, "primary_key_column": "id", "columns": columns},
        )
        if rows_csv is not None:
            _write(root / "tables" / tname / "data.csv", rows_csv)
    for aname in agents or []:
        _write(root / "agents" / aname / f"{aname}.json", {"name": aname, "instruction": "Hi."})
    return pack_bundle(root)


async def _upload(client, pod_id, zip_bytes) -> str:
    res = await client.post(
        f"/pods/{pod_id}/bundle/imports",
        files={"data": ("bundle.zip", zip_bytes, "application/zip")},
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    return res.json()["import_id"]


async def _wait(client, pod_id, import_id, *, until, timeout=90) -> dict:
    for _ in range(timeout):
        res = await client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
        assert res.status_code == status.HTTP_200_OK, res.text
        body = res.json()
        if body["status"] in until:
            return body
        await asyncio.sleep(1)
    raise AssertionError(f"Import stuck at {body['status']} (wanted {until})")


async def _new_pod(client, org_id) -> str:
    res = await client.post(
        "/pods",
        json={
            "name": f"Apply Target {uuid4()}",
            "slug": f"apply-target-{uuid4()}",
            "type": "ASSISTANT",
            "organization_id": org_id,
        },
        follow_redirects=True,
    )
    assert res.status_code == status.HTTP_201_CREATED, res.text
    return res.json()["id"]


_COLS = [
    {"name": "id", "type": "UUID", "required": True},
    {"name": "title", "type": "TEXT", "required": True},
]


async def test_apply_creates_resources_and_records_recipe(
    authenticated_client, test_pod, worker, tmp_path
):
    pod_id = test_pod["id"]
    zip_bytes = _make_bundle(
        tmp_path,
        table=("leads", _COLS),
        rows_csv="title\nAcme\nGlobex\n",
        agents=["assistant"],
    )
    import_id = await _upload(authenticated_client, pod_id, zip_bytes)
    await _wait(authenticated_client, pod_id, import_id, until={"AWAITING_CONFIRMATION"})

    apply = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={}
    )
    assert apply.status_code == status.HTTP_202_ACCEPTED, apply.text
    final = await _wait(authenticated_client, pod_id, import_id, until={"COMPLETED", "FAILED"})
    assert final["status"] == "COMPLETED", final

    # Table created + seeded.
    tbl = await authenticated_client.get(f"/pods/{pod_id}/datastore/tables/leads")
    assert tbl.status_code == status.HTTP_200_OK, tbl.text
    recs = await authenticated_client.get(f"/pods/{pod_id}/datastore/tables/leads/records")
    titles = {r["title"] for r in recs.json()["items"]}
    assert {"Acme", "Globex"} <= titles

    # Agent created.
    agent = await authenticated_client.get(f"/pods/{pod_id}/agents/assistant")
    assert agent.status_code == status.HTTP_200_OK, agent.text

    # Recipe recorded on the pod config.
    pod = await authenticated_client.get(f"/pods/{pod_id}")
    recipes = pod.json()["config"].get("recipes", [])
    assert any(r["kind"] == "upload" for r in recipes), pod.json()["config"]


async def test_apply_idempotent_on_reapply(authenticated_client, test_pod, worker, tmp_path):
    pod_id = test_pod["id"]
    zip_bytes = _make_bundle(tmp_path, table=("clients", _COLS), agents=["helper"])

    for _ in range(2):
        import_id = await _upload(authenticated_client, pod_id, zip_bytes)
        await _wait(authenticated_client, pod_id, import_id, until={"AWAITING_CONFIRMATION"})
        await authenticated_client.post(
            f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={}
        )
        final = await _wait(authenticated_client, pod_id, import_id, until={"COMPLETED", "FAILED"})
        assert final["status"] == "COMPLETED", final

    # Second import saw the resources as UPDATE, not CREATE — no duplication.
    tables = await authenticated_client.get(f"/pods/{pod_id}/datastore/tables")
    names = [t["name"] for t in tables.json()["items"]]
    assert names.count("clients") == 1


async def test_apply_destructive_requires_confirmation(
    authenticated_client, fixed_test_org, worker, tmp_path
):
    pod_id = await _new_pod(authenticated_client, fixed_test_org["id"])
    # Seed a table with a `score` column the bundle drops.
    create = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": "leads",
            "primary_key_column": "id",
            "enable_rls": True,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
                {"name": "score", "type": "INTEGER"},
            ],
        },
    )
    assert create.status_code == status.HTTP_201_CREATED, create.text

    zip_bytes = _make_bundle(tmp_path, table=("leads", _COLS))
    import_id = await _upload(authenticated_client, pod_id, zip_bytes)
    await _wait(authenticated_client, pod_id, import_id, until={"AWAITING_CONFIRMATION"})

    # Unconfirmed destructive apply is rejected.
    rej = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={}
    )
    assert rej.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, rej.text
    assert rej.json()["code"] == "POD_BUNDLE_CONFIRMATION_REQUIRED"

    # Confirmed apply proceeds and drops the column.
    ok = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply",
        json={"confirm_destructive": True},
    )
    assert ok.status_code == status.HTTP_202_ACCEPTED, ok.text
    final = await _wait(authenticated_client, pod_id, import_id, until={"COMPLETED", "FAILED"})
    assert final["status"] == "COMPLETED", final

    tbl = await authenticated_client.get(f"/pods/{pod_id}/datastore/tables/leads")
    col_names = {c["name"] for c in tbl.json()["columns"]}
    assert "score" not in col_names


async def test_cancel_deletes_import(authenticated_client, test_pod, worker, tmp_path):
    pod_id = test_pod["id"]
    zip_bytes = _make_bundle(tmp_path, table=("temp", _COLS))
    import_id = await _upload(authenticated_client, pod_id, zip_bytes)
    await _wait(authenticated_client, pod_id, import_id, until={"AWAITING_CONFIRMATION"})

    cancel = await authenticated_client.delete(f"/pods/{pod_id}/bundle/imports/{import_id}")
    assert cancel.status_code == status.HTTP_204_NO_CONTENT, cancel.text
    gone = await authenticated_client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
    assert gone.status_code == status.HTTP_410_GONE


@pytest.mark.workspace
async def test_export_then_import_apply_roundtrip(
    authenticated_client, test_pod, fixed_test_org, worker, workspace_api, tmp_path
):
    """Full cross-pod flow: build a source pod (function needs the agentbox),
    export it, import the real bundle into a fresh pod, apply, verify."""
    source_id = test_pod["id"]
    func_name = f"upper_{uuid4().hex[:6]}"
    await _create_function(authenticated_client, source_id, func_name)
    await authenticated_client.post(
        f"/pods/{source_id}/agents",
        json={"name": "greeter", "instruction": "Greet."},
        follow_redirects=True,
    )

    # Export the source pod.
    start = await authenticated_client.post(
        f"/pods/{source_id}/bundle/exports", json={"with_data": True}
    )
    export_id = start.json()["export_id"]
    for _ in range(60):
        st = await authenticated_client.get(f"/pods/{source_id}/bundle/exports/{export_id}")
        if st.json()["status"] in ("READY", "FAILED"):
            break
        await asyncio.sleep(1)
    assert st.json()["status"] == "READY"
    dl = await authenticated_client.get(
        f"/pods/{source_id}/bundle/exports/{export_id}/download"
    )
    assert dl.status_code == status.HTTP_200_OK

    # Import into a fresh pod and apply.
    target_id = await _new_pod(authenticated_client, fixed_test_org["id"])
    import_id = await _upload(authenticated_client, target_id, dl.content)
    await _wait(authenticated_client, target_id, import_id, until={"AWAITING_CONFIRMATION"})
    await authenticated_client.post(
        f"/pods/{target_id}/bundle/imports/{import_id}/apply", json={}
    )
    final = await _wait(authenticated_client, target_id, import_id, until={"COMPLETED", "FAILED"})
    assert final["status"] == "COMPLETED", final

    got = await authenticated_client.get(f"/pods/{target_id}/functions/{func_name}")
    assert got.status_code == status.HTTP_200_OK, got.text
    greeter = await authenticated_client.get(f"/pods/{target_id}/agents/greeter")
    assert greeter.status_code == status.HTTP_200_OK


async def _create_function(client, pod_id, func_name):
    code = f"""#input_type_name: In
#output_type_name: Out
#function_name: {func_name}

from pydantic import BaseModel
from lemma_sdk import FunctionContext

class In(BaseModel):
    text: str

class Out(BaseModel):
    result: str

async def {func_name}(ctx: FunctionContext, data: In) -> Out:
    return Out(result=data.text.upper())"""
    res = await client.post(
        f"/pods/{pod_id}/functions",
        json={"name": func_name, "description": "roundtrip", "code": code},
        follow_redirects=True,
    )
    assert res.status_code == status.HTTP_201_CREATED, res.text
