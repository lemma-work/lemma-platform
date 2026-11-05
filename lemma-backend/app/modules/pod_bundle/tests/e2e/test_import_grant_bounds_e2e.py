"""Use-case e2e: a bundle grants a workload only what its importer could.

An import writes agent and function grants. Those writes are bounded like the
permissions routes are: the importer may hand an agent only what they could use
themselves (audit H13), whoever wrote the bundle -- and the plan says so, and
the apply is refused with the pod untouched, rather than failing at the grants
step after the agent was created. And a grant on a connector the
pod does not declare and the organization never installed is refused while
planning, before anything is written -- it used to fail at the grants step with
"Unknown names", after the agent it grants to had been created.
"""

from __future__ import annotations

import json
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.datastore.tests.e2e.harness import auth_headers, invite_to_pod
from app.modules.test_support.e2e.waiters import wait_for_status
from app.modules.test_support.e2e_authz import signup_user
from lemma_pod_bundle import pack_bundle

pytestmark = [pytest.mark.e2e, pytest.mark.worker]


def _write(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _agent_bundle(tmp_path: Path, agent: str, grant: dict) -> bytes:
    root = tmp_path / f"bundle-{uuid4().hex[:8]}"
    _write(root / "pod.json", {"name": "Grants", "format_version": 2})
    _write(
        root / "agents" / agent / f"{agent}.json",
        {
            "name": agent,
            "instruction": "Tidy up.",
            "permissions": {"grants": [grant]},
        },
    )
    return pack_bundle(root)


async def _import(client, pod_id: str, zip_bytes: bytes, headers) -> str:
    up = await client.post(
        f"/pods/{pod_id}/bundle/uploads",
        files={"data": ("bundle.zip", zip_bytes, "application/zip")},
        headers=headers,
    )
    assert up.status_code == status.HTTP_201_CREATED, up.text
    res = await client.post(
        f"/pods/{pod_id}/bundle/imports",
        json={"kind": "URL", "url": up.json()["url"]},
        headers=headers,
    )
    assert res.status_code == status.HTTP_202_ACCEPTED, res.text
    return res.json()["import_id"]


async def _wait(client, pod_id: str, import_id: str, headers, *, until) -> dict:
    async def probe() -> dict:
        res = await client.get(
            f"/pods/{pod_id}/bundle/imports/{import_id}", headers=headers
        )
        assert res.status_code == status.HTTP_200_OK, res.text
        return res.json()

    return await wait_for_status(
        label=f"bundle import {import_id} to reach {until}",
        probe=probe,
        expected=set(until),
        failed=set(),
        timeout_seconds=120,
        interval_seconds=0.15,
    )


async def _editor(authenticated_client, async_client, org_id: str, pod_id: str):
    editor = await signup_user(async_client, f"bundle-grant-editor-{uuid4().hex[:6]}")
    await invite_to_pod(
        authenticated_client,
        async_client,
        org_id=org_id,
        pod_id=pod_id,
        user=editor,
        role="POD_EDITOR",
    )
    return auth_headers(editor["token"])


async def test_an_editor_cannot_import_an_agent_holding_a_delete_they_lack(
    authenticated_client, async_client, fixed_test_org, test_pod, worker, tmp_path
):
    pod_id = test_pod["id"]
    table = f"ledger_{uuid4().hex[:6]}"
    created = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": table,
            "primary_key_column": "id",
            "columns": [{"name": "id", "type": "UUID", "required": True}],
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    editor = await _editor(
        authenticated_client, async_client, fixed_test_org["id"], pod_id
    )
    zip_bytes = _agent_bundle(
        tmp_path,
        "cleaner",
        {
            "resource_type": "datastore_table",
            "resource_name": table,
            "permission_ids": ["datastore.table.delete"],
        },
    )

    import_id = await _import(async_client, pod_id, zip_bytes, editor)
    planned = await _wait(
        async_client, pod_id, import_id, editor, until={"AWAITING_CONFIRMATION"}
    )
    [grants_step] = [s for s in planned["plan"]["steps"] if s["kind"] == "AGENT_GRANTS"]
    assert "datastore.table.delete" in (grants_step.get("error") or ""), grants_step
    assert any("datastore.table.delete" in w for w in planned["plan"]["warnings"])

    apply = await async_client.post(
        f"/pods/{pod_id}/bundle/imports/{import_id}/apply",
        json={"variables": {}},
        headers=editor,
    )
    assert apply.status_code == status.HTTP_403_FORBIDDEN, apply.text
    assert apply.json()["code"] == "CONFERRAL_EXCEEDS_HOLDER", apply.text
    # Refused with the pod untouched: the agent it would have armed is not there.
    agent = await authenticated_client.get(f"/pods/{pod_id}/agents/cleaner")
    assert agent.status_code == status.HTTP_404_NOT_FOUND, agent.text


async def test_a_grant_on_a_connector_nobody_installed_is_refused_at_planning(
    authenticated_client, test_pod, worker, tmp_path
):
    pod_id = test_pod["id"]
    missing = f"nowhere-{uuid4().hex[:6]}"
    zip_bytes = _agent_bundle(
        tmp_path,
        "courier",
        {
            "resource_type": "connector",
            "resource_name": missing,
            "permission_ids": ["connector.use"],
        },
    )

    import_id = await _import(authenticated_client, pod_id, zip_bytes, None)
    planned = await _wait(
        authenticated_client,
        pod_id,
        import_id,
        None,
        until={"AWAITING_CONFIRMATION", "FAILED"},
    )

    assert planned["status"] == "FAILED", planned
    assert missing in (planned.get("error") or ""), planned
    assert "agent 'courier'" in planned["error"], planned
    # Refused before anything was written.
    agent = await authenticated_client.get(f"/pods/{pod_id}/agents/courier")
    assert agent.status_code == status.HTTP_404_NOT_FOUND, agent.text
