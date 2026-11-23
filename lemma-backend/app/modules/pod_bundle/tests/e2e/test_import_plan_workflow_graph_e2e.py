"""A bundled workflow graph the platform would refuse fails the plan.

A LOOP with two outgoing edges (one to its body, one to the step after) used
to plan cleanly; the apply then created the table before it and failed at the
WORKFLOW step. The plan now asks the workflow module's own graph rules first.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import wait_import
from lemma_pod_bundle import pack_bundle

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


def _write(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _loop_bundle(tmp_path: Path, table: str) -> bytes:
    root = tmp_path / f"bundle-{uuid4().hex[:8]}"
    _write(root / "pod.json", {"name": "Digest", "format_version": 2})
    _write(
        root / "tables" / table / f"{table}.json",
        {
            "name": table,
            "primary_key_column": "id",
            "columns": [{"name": "title", "type": "TEXT"}],
        },
    )
    _write(
        root / "workflows" / "digest" / "digest.json",
        {
            "name": "digest",
            "nodes": [
                {
                    "id": "each",
                    "type": "LOOP",
                    "config": {"items_path": "start.items", "child_node_id": "work"},
                },
                {"id": "work", "type": "FUNCTION", "config": {"function_name": "f"}},
                {"id": "after", "type": "END"},
            ],
            "edges": [
                {"id": "e1", "source": "each", "target": "work"},
                {"id": "e2", "source": "each", "target": "after"},
            ],
        },
    )
    return pack_bundle(root)


async def test_a_loop_with_two_outgoing_edges_fails_the_plan_with_nothing_written(
    authenticated_client, test_pod, worker, tmp_path
):
    pod_id = test_pod["id"]
    table = f"items_{uuid4().hex[:6]}"
    up = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/uploads",
        files={
            "data": ("bundle.zip", _loop_bundle(tmp_path, table), "application/zip")
        },
    )
    assert up.status_code == status.HTTP_201_CREATED, up.text
    started = await authenticated_client.post(
        f"/pods/{pod_id}/bundle/imports", json={"kind": "URL", "url": up.json()["url"]}
    )
    assert started.status_code == status.HTTP_202_ACCEPTED, started.text

    planned = await wait_import(
        authenticated_client,
        pod_id,
        started.json()["import_id"],
        until={"AWAITING_CONFIRMATION", "FAILED"},
    )

    assert planned["status"] == "FAILED", planned
    assert "Workflow 'digest'" in (planned.get("error") or ""), planned
    assert "loop 'each' has 2 outgoing edges" in planned["error"], planned
    tables = await authenticated_client.get(f"/pods/{pod_id}/datastore/tables")
    assert tables.status_code == status.HTTP_200_OK, tables.text
    assert table not in {t["name"] for t in tables.json()["items"]}
