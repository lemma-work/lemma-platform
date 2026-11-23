"""A contact-owned table survives an import, and a second import of it.

The server-side applier used to create such a table as an ordinary one: the
contact policy was never set, and the next import of the same bundle planned
to drop the `contact_id` column the platform had added.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.test_support.e2e.waiters import wait_for_status
from lemma_pod_bundle import pack_bundle

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


def _bundle(tmp_path: Path) -> bytes:
    root = tmp_path / f"bundle-{uuid4().hex[:8]}"
    root.mkdir(parents=True)
    (root / "pod.json").write_text(
        json.dumps({"name": "Visitors", "format_version": 2, "variables": {}})
    )
    table_dir = root / "tables" / "visitors"
    table_dir.mkdir(parents=True)
    (table_dir / "visitors.json").write_text(
        json.dumps(
            {
                "name": "visitors",
                "primary_key_column": "id",
                "enable_rls": False,
                "contact_owned": True,
                "contact_columns": ["title"],
                "columns": [
                    {"name": "id", "type": "UUID", "required": True},
                    {"name": "title", "type": "TEXT"},
                ],
            }
        )
    )
    return pack_bundle(root)


async def _plan(client, pod_id: str, zip_bytes: bytes) -> tuple[str, dict]:
    up = await client.post(
        f"/pods/{pod_id}/bundle/uploads",
        files={"data": ("bundle.zip", zip_bytes, "application/zip")},
    )
    assert up.status_code == status.HTTP_201_CREATED, up.text
    res = await client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "URL", "url": up.json()["url"]},
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    import_id = res.json()["import_id"]
    body = await _wait(client, pod_id, import_id, {"AWAITING_CONFIRMATION"})
    return import_id, body


async def _wait(client, pod_id: str, import_id: str, until: set[str]) -> dict:
    async def probe() -> dict:
        res = await client.get(f"/pods/{pod_id}/bundle/imports/{import_id}")
        assert res.status_code == status.HTTP_200_OK, res.text
        return res.json()

    return await wait_for_status(
        label=f"bundle import {import_id} to reach {until}",
        probe=probe,
        expected=until,
        failed=set(),
        timeout_seconds=90,
        interval_seconds=0.15,
    )


async def test_contact_owned_table_imports_and_reimports_cleanly(
    authenticated_client, test_pod, worker, tmp_path
):
    client = authenticated_client
    pod_id = test_pod["id"]
    zip_bytes = _bundle(tmp_path)

    import_id, _ = await _plan(client, pod_id, zip_bytes)
    applied = await client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply", json={}
    )
    assert applied.status_code == status.HTTP_202_ACCEPTED, applied.text
    final = await _wait(client, pod_id, import_id, {"COMPLETED", "FAILED"})
    assert final["status"] == "COMPLETED", final

    table = await client.get(f"/pods/{pod_id}/datastore/tables/visitors")
    assert table.status_code == status.HTTP_200_OK, table.text
    detail = table.json()
    assert detail["contact_owned"] is True
    assert detail["contact_columns"] == ["title"]
    assert "contact_id" in {c["name"] for c in detail["columns"]}

    _, again = await _plan(client, pod_id, zip_bytes)
    step = next(s for s in again["plan"]["steps"] if s["kind"] == "TABLE")
    assert step["destructive"] is False, step
    assert "contact_id" not in (step.get("detail") or {}).get("columns_to_remove", [])
