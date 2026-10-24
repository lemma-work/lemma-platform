"""E2E: a Lemma starts in one call, whole or not at all.

``POST /pods/start`` makes the pod, sets what it owns, writes its first
``/plan.md`` and opens the chat its agent sets itself up in. From the person's
own words the agent's turn is queued at once. From a template the chat is held
until a worker has applied the template's tables, skill and schedules, and the
job that applied them starts the turn. A failure part way leaves no pod.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.config import settings
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.work import WorkClass
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.agent.events.work_kinds import turn_key
from app.modules.pod_bundle.events.template_setup import SET_UP_LEMMA
from app.modules.pod_bundle.events.work import plan_key
from app.modules.pod_bundle.tests.e2e.bundle_e2e_helpers import wait_import
from app.modules.pod_bundle.tests.e2e.bundle_work_support import (
    rows,
    scalar,
    table_names,
)

pytestmark = [pytest.mark.e2e]


async def _start(client, body: dict[str, object], expected: int) -> dict:
    res = await client.post("/pods/start", json=body)
    assert res.status_code == expected, res.text
    return res.json()


async def _plan(client, pod_id: str) -> str:
    res = await client.get(
        f"/pods/{pod_id}/datastore/files/download", params={"path": "/plan.md"}
    )
    assert res.status_code == status.HTTP_200_OK, res.text
    return res.text


async def _first_message(client, pod_id: str, conversation_id: str) -> str:
    res = await client.get(f"/pods/{pod_id}/conversations/{conversation_id}/messages")
    assert res.status_code == status.HTTP_200_OK, res.text
    said = [m["text"] for m in res.json()["items"] if m["role"].lower() == "user"]
    assert said, res.text
    return str(said[0])


async def _run_of(conversation_id: str) -> UUID:
    found = await rows(
        "SELECT id FROM agent_runs WHERE conversation_id = :c", c=UUID(conversation_id)
    )
    assert len(found) == 1
    return found[0][0]


async def _items(*dedup_keys: str) -> dict[str, str]:
    """Live work items by dedup key, with their kind."""
    found = await rows(
        "SELECT dedup_key, kind FROM work_items "
        "WHERE dedup_key = ANY(:keys) AND state <> 'dead'",
        keys=list(dedup_keys),
    )
    return {row[0]: row[1] for row in found}


async def _pods_named(org_id: str, name: str) -> int:
    return int(
        await scalar(
            "SELECT count(*) FROM pods WHERE organization_id = :o AND name = :n",
            o=UUID(org_id),
            n=name,
        )
    )


@pytest.mark.asyncio
async def test_from_its_own_words_everything_is_there_and_the_agent_is_asked(
    authenticated_client, fixed_test_org
):
    name = f"Approvals {uuid4().hex[:6]}"
    started = await _start(
        authenticated_client,
        {
            "organization_id": fixed_test_org["id"],
            "name": name,
            "outcome": {
                "sentence": "Every invoice over 10000 gets a second approval.",
                "kind": "week",
                "target": 100,
                "unit": "%",
            },
        },
        status.HTTP_201_CREATED,
    )
    pod_id, conversation_id = started["pod"]["id"], started["conversation_id"]
    assert started["import_id"] is None
    assert started["pod"]["name"] == name

    pod = (await authenticated_client.get(f"/pods/{pod_id}")).json()
    assert pod["config"]["outcome"]["sentence"].startswith("Every invoice")
    assert pod["config"]["outcome"]["since"] == started["pod"]["outcome"]["since"]

    plan = await _plan(authenticated_client, pod_id)
    assert "## What must be true\n- Every invoice over 10000" in plan
    assert "## Never" in plan

    said = await _first_message(authenticated_client, pod_id, conversation_id)
    assert "Set yourself up to own this" in said and "set_what_you_own" in said
    run_id = await _run_of(conversation_id)
    # Queued with everything else -- unless a session worker has run it already.
    staged = await _items(turn_key(run_id))
    finished = await scalar(
        "SELECT status <> 'RUNNING' FROM agent_runs WHERE id = :r", r=run_id
    )
    assert staged or finished


@pytest.mark.asyncio
async def test_two_lemmas_from_one_template_get_two_names(
    authenticated_client, fixed_test_org
):
    body = {
        "organization_id": fixed_test_org["id"],
        "template": "reviews",
        "name": f"Reviews {uuid4().hex[:6]}",
    }
    first = await _start(authenticated_client, body, status.HTTP_201_CREATED)
    second = await _start(authenticated_client, body, status.HTTP_201_CREATED)
    assert second["pod"]["name"] == f"{first['pod']['name']} 2"


@pytest.mark.asyncio
@pytest.mark.drives_work
async def test_from_a_template_the_agent_starts_once_the_template_is_in(
    authenticated_client, fixed_test_org
):
    due = date.today() + timedelta(days=30)
    started = await _start(
        authenticated_client,
        {
            "organization_id": fixed_test_org["id"],
            "template": "launch",
            "name": f"Launch {uuid4().hex[:6]}",
            "due": due.isoformat(),
        },
        status.HTTP_201_CREATED,
    )
    pod_id, import_id = started["pod"]["id"], started["import_id"]
    outcome = started["pod"]["outcome"]
    assert outcome["kind"] == "date" and outcome["due"] == due.isoformat()
    assert outcome["measure"] and "{due}" not in outcome["sentence"]
    run_id = await _run_of(started["conversation_id"])

    # Held: while the template job is queued, the agent's turn is not.
    keys = (plan_key(UUID(import_id)), turn_key(run_id))
    before = await _items(*keys)
    if before.get(keys[0]) == SET_UP_LEMMA:
        assert keys[1] not in before

    await run_until_idle(async_session_maker, classes=[WorkClass.ADMIN])
    await wait_import(authenticated_client, pod_id, import_id, until={"COMPLETED"})
    assert "steps" in await table_names(pod_id)
    plan = await _plan(authenticated_client, pod_id)
    assert "## How we work\n- Every step to the launch is a row in steps" in plan

    after = await _items(*keys)
    finished = await scalar(
        "SELECT status <> 'RUNNING' FROM agent_runs WHERE id = :r", r=run_id
    )
    assert keys[0] not in after
    assert keys[1] in after or finished, "the template job started the turn"

    standing = (
        await authenticated_client.get(f"/pods/{pod_id}/outcome/standing")
    ).json()
    assert standing["value"] == 0.0, "the template's measure reads its own table"


@pytest.mark.asyncio
async def test_a_template_finish_line_needs_its_date_and_an_unknown_one_is_404(
    authenticated_client, fixed_test_org
):
    org = fixed_test_org["id"]
    missing = await _start(
        authenticated_client,
        {"organization_id": org, "template": "launch"},
        status.HTTP_422_UNPROCESSABLE_CONTENT,
    )
    assert "due" in missing["message"]
    unknown = await _start(
        authenticated_client,
        {"organization_id": org, "template": "no-such-template"},
        status.HTTP_404_NOT_FOUND,
    )
    assert unknown["code"] == "POD_BUNDLE_TEMPLATE_NOT_FOUND"


@pytest.mark.asyncio
async def test_a_failure_part_way_leaves_no_pod_behind(
    authenticated_client, fixed_test_org, tmp_path: Path, monkeypatch
):
    """The plan file is written after the pod and what it owns; with storage
    unwritable that write fails, and the pod goes with it."""
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    if settings.effective_storage_backend() == "local" and settings.storage_bucket:
        monkeypatch.setattr(settings, "storage_bucket", f"{locked}/store")
    else:
        monkeypatch.setattr(settings, "local_object_storage_root", f"{locked}/store")
    name = f"Nothing {uuid4().hex[:6]}"
    try:
        # The in-process client re-raises what a server answers 500 with.
        with pytest.raises(OSError):
            await authenticated_client.post(
                "/pods/start",
                json={
                    "organization_id": fixed_test_org["id"],
                    "name": name,
                    "outcome": {
                        "sentence": "Every bill is paid on time.",
                        "kind": "week",
                    },
                },
            )
    finally:
        locked.chmod(0o700)
    assert await _pods_named(fixed_test_org["id"], name) == 0
