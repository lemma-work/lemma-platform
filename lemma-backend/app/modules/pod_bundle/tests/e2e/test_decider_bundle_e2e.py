"""A decider through a real export and import, end to end.

The source pod's decider has been taught -- a person corrected one of its
decisions, which made that answer an example. The bundle has to carry the
definition and nothing it learned; the importing pod has to end up with the same
definition and no examples; and importing the same bundle again has to save no
new version, while a changed one saves the next.
"""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID

import pytest
from fastapi import status
from sqlalchemy import text

from lemma_pod_bundle import extract_bundle, pack_bundle
from lemma_pod_bundle.normalize import _without_nulls

from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import (
    import_and_apply,
    new_pod,
    start_and_plan_import,
    wait_import,
)
from app.modules.test_support.e2e.waiters import wait_for_status

pytestmark = [pytest.mark.e2e, pytest.mark.worker]

TRIAGE = {
    "description": "What Kit does with each new email.",
    "input": {"fields": ["from", "subject", "labels"], "max_chars": 2000},
    "questions": {
        "action": {
            "type": "choice",
            "prompt": "What should Kit do with this email?",
            "options": {
                "act": "A customer is waiting, or an invoice needs filing.",
                "ask": "Needs a person.",
                "ignore": "Newsletters and promotions.",
            },
            "fallback": "ask",
        }
    },
    "rules": [
        {"when": "contains(labels, 'PROMOTIONS')", "answer": {"action": "ignore"}}
    ],
}
# What the decider was asked about in the source pod. A person's answer to it
# is the example that must never reach a bundle.
INVOICE = {
    "from": "billing@acme.example",
    "subject": "Invoice 4471 is overdue",
    "labels": ["INBOX"],
}
ASK_THE_DECIDER = {
    "resource_type": "decider",
    "resource_name": "email-triage",
    "permission_ids": ["decider.execute"],
}


async def _examples(db_session, pod_id: str) -> int:
    counted = await db_session.execute(
        text("SELECT count(*) FROM decision_examples WHERE pod_id = :pod_id"),
        {"pod_id": UUID(pod_id)},
    )
    return int(counted.scalar_one())


async def _teach(client, pod_id: str) -> None:
    """Ask the decider about an email, then answer as a person, which keeps the
    answer as one of the decider's examples."""
    asked = await client.post(
        f"/pods/{pod_id}/decisions",
        json={"decider": "email-triage", "state": INVOICE},
    )
    assert asked.status_code == status.HTTP_200_OK, asked.text
    answered = await client.post(
        f"/pods/{pod_id}/decisions/{asked.json()['id']}/answer",
        json={"answers": {"action": "ask"}},
    )
    assert answered.status_code == status.HTTP_200_OK, answered.text


async def _export(client, pod_id: str) -> bytes:
    start = await client.post(f"/pods/{pod_id}/bundle/exports", json={})
    assert start.status_code == status.HTTP_202_ACCEPTED, start.text
    export_id = start.json()["export_id"]

    async def probe() -> dict:
        res = await client.get(f"/pods/{pod_id}/bundle/exports/{export_id}")
        assert res.status_code == status.HTTP_200_OK, res.text
        return res.json()

    ready = await wait_for_status(
        label=f"pod {pod_id} bundle export {export_id}",
        probe=probe,
        expected={"READY"},
        timeout_seconds=60,
        interval_seconds=0.15,
    )
    parts = urlsplit(ready["download_url"])
    download = await client.get(f"{parts.path}?{parts.query}")
    assert download.status_code == status.HTTP_200_OK, download.text
    return download.content


async def _decider(client, pod_id: str) -> dict:
    res = await client.get(f"/pods/{pod_id}/deciders/email-triage")
    assert res.status_code == status.HTTP_200_OK, res.text
    return res.json()


def _bundle_text(root: Path) -> str:
    return "\n".join(
        path.read_text(encoding="utf-8", errors="ignore")
        for path in sorted(root.rglob("*"))
        if path.is_file()
    )


async def test_a_decider_travels_as_its_definition_and_leaves_its_examples_home(
    authenticated_client, fixed_test_org, worker, db_session, tmp_path
):
    client = authenticated_client
    source = await new_pod(client, fixed_test_org["id"], label="Decider Source")
    created = await client.post(
        f"/pods/{source}/deciders",
        json={"name": "email-triage", "definition": TRIAGE},
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    await _teach(client, source)
    assert await _examples(db_session, source) == 1
    agent = await client.post(
        f"/pods/{source}/agents",
        json={"name": "triager", "instruction": "Sort the inbox."},
        follow_redirects=True,
    )
    assert agent.status_code == status.HTTP_201_CREATED, agent.text
    granted = await client.put(
        f"/pods/{source}/agents/triager/permissions",
        json={"grants": [ASK_THE_DECIDER]},
    )
    assert granted.status_code == status.HTTP_200_OK, granted.text
    definition = (await _decider(client, source))["definition"]

    zip_bytes = await _export(client, source)

    root = extract_bundle(zip_bytes, tmp_path / "bundle")
    manifest_path = root / "deciders" / "email-triage" / "email-triage.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest == {
        "name": "email-triage",
        "definition": _without_nulls(definition),
    }
    everything = _bundle_text(root)
    assert "Invoice 4471" not in everything
    assert "billing@acme.example" not in everything

    target = await new_pod(client, fixed_test_org["id"], label="Decider Target")
    await import_and_apply(client, target, zip_bytes)

    imported = await _decider(client, target)
    assert imported["version"] == 1
    assert imported["definition"] == definition
    assert await _examples(db_session, target) == 0
    permissions = await client.get(f"/pods/{target}/agents/triager/permissions")
    assert permissions.status_code == status.HTTP_200_OK, permissions.text
    assert ASK_THE_DECIDER in permissions.json()["grants"]

    # The same bundle again is a no-op for the decider: planned SKIP, and no
    # version saved.
    import_id = await start_and_plan_import(client, target, zip_bytes)
    planned = await client.get(f"/pods/{target}/bundle/imports/{import_id}")
    [step] = [s for s in planned.json()["plan"]["steps"] if s["kind"] == "DECIDER"]
    assert (step["action"], step["status"]) == ("SKIP", "SKIPPED")
    applied = await client.post(
        f"/pods/{target}/bundle/imports/{import_id}/apply", json={"variables": {}}
    )
    assert applied.status_code == status.HTTP_202_ACCEPTED, applied.text
    final = await wait_import(client, target, import_id, until={"COMPLETED", "FAILED"})
    assert final["status"] == "COMPLETED", final
    assert (await _decider(client, target))["version"] == 1

    # A changed definition saves the next version, and the first is kept.
    manifest["definition"]["guidance"] = "Invoices are Kit's to file."
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    await import_and_apply(client, target, pack_bundle(root))

    revised = await _decider(client, target)
    assert revised["version"] == 2
    assert revised["definition"]["guidance"] == "Invoices are Kit's to file."
    versions = await client.get(f"/pods/{target}/deciders/email-triage/versions")
    assert [item["version"] for item in versions.json()["items"]] == [2, 1]
