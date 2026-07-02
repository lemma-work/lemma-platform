"""End-to-end pod bundle import planning.

Real ASGI app + the real streaq worker subprocess run the ``plan_pod_import``
job. Bundles are built in-process with ``lemma_pod_bundle.pack_bundle`` (no
agentbox needed), so these tests are ``worker``-marked, not ``workspace``: the
planner only lists+diffs the pod's resources, it never runs a sandbox.
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


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _make_bundle(
    tmp_path: Path,
    *,
    name: str = "Imported CRM",
    tables: dict[str, list[dict]] | None = None,
    agents: list[str] | None = None,
) -> bytes:
    root = tmp_path / f"bundle-{uuid4().hex[:8]}"
    _write(root / "pod.json", {"name": name, "format_version": 2, "variables": {}})
    for tname, columns in (tables or {}).items():
        _write(
            root / "tables" / tname / f"{tname}.json",
            {"name": tname, "primary_key_column": "id", "columns": columns},
        )
    for aname in agents or []:
        _write(
            root / "agents" / aname / f"{aname}.json",
            {"name": aname, "instruction": "Answer briefly."},
        )
    return pack_bundle(root)


async def _new_pod(authenticated_client, org_id: str) -> str:
    res = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Import Target {uuid4()}",
            "slug": f"import-target-{uuid4()}",
            "type": "ASSISTANT",
            "organization_id": org_id,
        },
        follow_redirects=True,
    )
    assert res.status_code == status.HTTP_201_CREATED, res.text
    return res.json()["id"]


async def _upload_import(authenticated_client, pod_id: str, zip_bytes: bytes) -> str:
    res = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports",
        files={"data": ("bundle.zip", zip_bytes, "application/zip")},
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    body = res.json()
    assert body["status"] in ("QUEUED", "PLANNING")
    return body["import_id"]


async def _wait_for_plan(authenticated_client, pod_id, import_id, timeout=60) -> dict:
    for _ in range(timeout):
        res = await authenticated_client.get(
            f"/pods/{pod_id}/bundle/imports/{import_id}"
        )
        assert res.status_code == status.HTTP_200_OK, res.text
        body = res.json()
        if body["status"] in ("AWAITING_CONFIRMATION", "FAILED"):
            return body
        await asyncio.sleep(1)
    raise AssertionError(f"Plan did not finish in {timeout}s")


async def _create_table(authenticated_client, pod_id, table_name, extra_columns) -> None:
    res = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table_name,
            "primary_key_column": "id",
            "enable_rls": True,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
                *extra_columns,
            ],
        },
    )
    assert res.status_code == status.HTTP_201_CREATED, res.text


async def test_plan_classifies_new_resources_as_create(
    authenticated_client, test_pod, fixed_test_org, worker, tmp_path
):
    pod_id = test_pod["id"]
    zip_bytes = _make_bundle(
        tmp_path,
        tables={"leads": [{"name": "id", "type": "UUID", "required": True}]},
        agents=["assistant"],
    )
    import_id = await _upload_import(authenticated_client, pod_id, zip_bytes)
    body = await _wait_for_plan(authenticated_client, pod_id, import_id)

    assert body["status"] == "AWAITING_CONFIRMATION", body
    plan = body["plan"]
    assert plan["format_version"] == 2
    steps = {(s["kind"], s["name"]): s for s in plan["steps"]}
    assert steps[("TABLE", "leads")]["action"] == "CREATE"
    assert steps[("AGENT", "assistant")]["action"] == "CREATE"
    assert plan["has_destructive_steps"] is False


async def test_plan_flags_destructive_column_drop(
    authenticated_client, fixed_test_org, worker, tmp_path
):
    # Pod already has a `leads` table with a `score` column the bundle omits.
    pod_id = await _new_pod(authenticated_client, fixed_test_org["id"])
    await _create_table(
        authenticated_client,
        pod_id,
        "leads",
        [{"name": "score", "type": "INTEGER", "required": False}],
    )
    zip_bytes = _make_bundle(
        tmp_path,
        tables={
            "leads": [
                {"name": "id", "type": "UUID", "required": True},
                {"name": "title", "type": "TEXT", "required": True},
            ]
        },
    )
    import_id = await _upload_import(authenticated_client, pod_id, zip_bytes)
    body = await _wait_for_plan(authenticated_client, pod_id, import_id)

    step = next(s for s in body["plan"]["steps"] if s["kind"] == "TABLE")
    assert step["action"] == "UPDATE"
    assert step["destructive"] is True
    assert "score" in step["detail"]["columns_to_remove"]
    assert body["plan"]["has_destructive_steps"] is True
    assert any("score" in w for w in body["plan"]["warnings"])


async def test_get_import_expired_returns_410(authenticated_client, test_pod, worker):
    pod_id = test_pod["id"]
    res = await authenticated_client.get(
        f"/pods/{pod_id}/bundle/imports/{uuid4()}"
    )
    assert res.status_code == status.HTTP_410_GONE, res.text
    assert res.json()["code"] == "POD_BUNDLE_EXPIRED"


async def test_import_rejects_non_zip(authenticated_client, test_pod, worker):
    pod_id = test_pod["id"]
    res = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports",
        files={"data": ("bundle.zip", b"not a zip", "application/zip")},
    )
    assert res.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY, res.text
    assert res.json()["code"] == "POD_BUNDLE_INVALID"


# The live SSE endpoint (snapshot-then-live frames) is covered deterministically
# by tests/unit/test_import_events.py — streaming a never-terminating server
# generator over the in-process ASGI transport hangs the shared session loop, so
# it is not exercised as an e2e here.
