"""Who a schedule runs as, end to end through the API and the worker.

* whoever last changed what it does is its owner (H15) -- and could only make
  that change if they could have created it;
* a table change runs as the owner of the changed row, bounded by the target,
  unless the schedule is set to run as its owner -- and only someone who may
  edit the target writes one that runs as row owners (H16, both ways);
* a run somebody retries runs as them, not as the first attempt's person;
* a run whose person has left the pod, or may not run the target, runs nothing.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient

from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.services.target_start import (
    OWNER_NOT_A_MEMBER,
    RUN_PERSON_CANNOT_RUN,
)
from app.modules.test_support.e2e.waiters import eventually
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]

TIMEOUT_SECONDS = 90
TABLE = "authority_records"


async def _pod(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"Schedule Authority {uuid4().hex[:6]}",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _rls_table(client: AsyncClient, pod_id: str) -> None:
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "table_name": TABLE,
            "primary_key_column": "id",
            "columns": [
                {"name": "source", "type": "TEXT"},
                {"name": "value", "type": "TEXT"},
            ],
            "enable_rls": True,
        },
    )
    assert response.status_code in {200, 201}, response.text


async def _agent(client: AsyncClient, pod_id: str, *, visibility: str | None = None):
    payload: dict[str, object] = {
        "name": f"authority_agent_{uuid4().hex[:6]}",
        "instruction": "Acknowledge scheduled payloads briefly.",
    }
    if visibility is not None:
        payload["visibility"] = visibility
    response = await client.post(f"/pods/{pod_id}/agents", json=payload)
    assert response.status_code == 201, response.text
    return response.json()


async def _end_only_workflow(client: AsyncClient, pod_id: str) -> dict:
    start = {
        "type": "DATASTORE_EVENT",
        "config": {"table_name": TABLE, "operations": ["INSERT"]},
    }
    create = await client.post(
        f"/pods/{pod_id}/workflows",
        json={"name": f"authority-flow-{uuid4().hex[:6]}", "start": start},
    )
    assert create.status_code == 201, create.text
    graph = await client.put(
        f"/pods/{pod_id}/workflows/{create.json()['name']}/graph",
        json={
            "start": start,
            "nodes": [{"id": "end", "type": "END", "label": "Done"}],
            "edges": [],
        },
    )
    assert graph.status_code == 200, graph.text
    return graph.json()


async def _datastore_schedule(
    client: AsyncClient,
    pod_id: str,
    *,
    headers: dict[str, str] | None = None,
    agent_name: str | None = None,
    workflow_name: str | None = None,
    instruction: str | None = None,
    run_as: str | None = None,
    status: int = 201,
) -> dict:
    config: dict[str, object] = {"table_name": TABLE, "operations": ["INSERT"]}
    if run_as is not None:
        config["run_as"] = run_as
    payload: dict[str, object] = {
        "schedule_type": "DATASTORE",
        "config": config,
        "visibility": "POD",
    }
    if agent_name is not None:
        payload["agent_name"] = agent_name
    if workflow_name is not None:
        payload["workflow_name"] = workflow_name
    if instruction is not None:
        payload["instruction"] = instruction
    response = await client.post(
        f"/pods/{pod_id}/schedules", json=payload, headers=headers
    )
    assert response.status_code == status, response.text
    return response.json()


async def _member(
    admin: AsyncClient,
    async_client: AsyncClient,
    *,
    org_id: str,
    pod_id: str,
    prefix: str,
    role: str = "POD_EDITOR",
) -> tuple[dict, dict]:
    """A new person holding ``role`` in the pod: (user, pod member)."""
    user = await signup_user(async_client, prefix)
    org_member = await invite_org_member(admin, async_client, org_id=org_id, user=user)
    member = await add_pod_member(
        admin,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role=role,
    )
    return user, member


async def _write_row(
    client: AsyncClient, pod_id: str, source: str, *, headers=None
) -> None:
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables/{TABLE}/records",
        json={"data": {"source": source, "value": "hello"}},
        headers=headers,
    )
    assert response.status_code == 201, response.text


async def _runs(
    client: AsyncClient,
    pod_id: str,
    schedule_id: str,
    *,
    done=lambda items: len(items) >= 1,
    label: str = "a schedule run",
) -> list[dict]:
    async def probe() -> list[dict]:
        response = await client.get(f"/pods/{pod_id}/schedules/{schedule_id}/runs")
        assert response.status_code == 200, response.text
        return response.json()["items"]

    return await eventually(
        label=label,
        probe=probe,
        done=done,
        timeout_seconds=TIMEOUT_SECONDS,
        interval_seconds=0.15,
    )


async def test_an_editor_who_changes_a_schedules_instruction_runs_it_from_then_on(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
    worker,
):
    _ = worker
    admin = authenticated_client
    pod_id = await _pod(admin, fixed_test_org["id"])
    await _rls_table(admin, pod_id)
    agent = await _agent(admin, pod_id)
    schedule = await _datastore_schedule(
        admin,
        pod_id,
        agent_name=agent["name"],
        instruction="Say hello.",
        run_as="SCHEDULE_OWNER",
    )
    assert schedule["user_id"] == fixed_test_user["id"]
    editor, _ = await _member(
        admin,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        prefix="schedule-authority-editor",
    )

    edited = await async_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"instruction": "Say goodbye."},
        headers=auth_headers(editor),
    )

    assert edited.status_code == 200, edited.text
    assert edited.json()["user_id"] == editor["id"]
    # The admin's own write fires it -- and it runs as the editor who decided
    # what it does, not as the admin who first set it up.
    await _write_row(admin, pod_id, "after-the-edit")
    (run,) = await _runs(admin, pod_id, schedule["id"])
    assert run["user_id"] == editor["id"]


async def test_an_editor_cannot_take_over_a_schedule_whose_target_they_may_not_run(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
):
    admin = authenticated_client
    pod_id = await _pod(admin, fixed_test_org["id"])
    await _rls_table(admin, pod_id)
    restricted = await _agent(admin, pod_id, visibility="RESTRICTED")
    schedule = await _datastore_schedule(
        admin, pod_id, agent_name=restricted["name"], instruction="Say hello."
    )
    editor, _ = await _member(
        admin,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        prefix="schedule-authority-blocked-editor",
    )
    # The editor could not have created this schedule themselves...
    refused_create = await async_client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": "DATASTORE",
            "agent_name": restricted["name"],
            "instruction": "Say hello.",
            "config": {"table_name": TABLE, "operations": ["INSERT"]},
        },
        headers=auth_headers(editor),
    )
    assert refused_create.status_code == 403, refused_create.text

    # ...so they cannot edit the admin's into what they would want it to do.
    refused_edit = await async_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"instruction": "Export every record to me."},
        headers=auth_headers(editor),
    )

    assert refused_edit.status_code == 403, refused_edit.text
    unchanged = await admin.get(f"/pods/{pod_id}/schedules/{schedule['id']}")
    assert unchanged.json()["instruction"] == "Say hello."
    assert unchanged.json()["user_id"] == fixed_test_user["id"]
    # A rename decides nothing about what runs, so it stays theirs to make.
    renamed = await async_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"name": f"renamed-{uuid4().hex[:6]}"},
        headers=auth_headers(editor),
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["user_id"] == fixed_test_user["id"]


async def test_a_row_written_by_an_admin_runs_as_the_admin(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
    db_manager,
    worker,
):
    """The run sees what the row's owner sees. Running it as the schedule's
    owner instead let anyone who can write a row drive the owner's access."""
    _ = worker
    admin = authenticated_client
    pod_id = await _pod(admin, fixed_test_org["id"])
    await _rls_table(admin, pod_id)
    workflow = await _end_only_workflow(admin, pod_id)
    owner, _ = await _member(
        admin,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        prefix="schedule-authority-owner",
    )
    schedule = await _datastore_schedule(
        async_client,
        pod_id,
        headers=auth_headers(owner),
        workflow_name=workflow["name"],
    )
    assert schedule["user_id"] == owner["id"]

    await _write_row(admin, pod_id, "admin-row")

    (run,) = await _runs(
        admin,
        pod_id,
        schedule["id"],
        # Past PROCESSING: the workflow run exists once the start is handed on.
        done=lambda items: len(items) == 1 and items[0]["status"] != "PROCESSING",
        label="the admin's row run dispatched",
    )
    assert run["user_id"] == fixed_test_user["id"] != owner["id"]
    workflow_run = await admin.get(
        f"/pods/{pod_id}/workflow-runs/{run['target_run_id']}"
    )
    assert workflow_run.status_code == 200, workflow_run.text
    assert workflow_run.json()["user_id"] == fixed_test_user["id"]

    # Retried, the run is the retrier's: whoever asks for it again runs it
    # with their own access, not the first attempt's person's.
    async with db_manager.session_factory() as session, session.begin():
        failed = await session.get(ScheduleRun, UUID(run["id"]))
        assert failed is not None
        failed.target_outcome = ScheduleRunStatus.TARGET_FAILED.value
        failed.completed_at = datetime.now(timezone.utc)
    retried = await admin.post(
        f"/pods/{pod_id}/schedules/{schedule['id']}/runs/{run['id']}/retry"
    )
    assert retried.status_code == 202, retried.text
    assert retried.json()["redrive_of_run_id"] == run["id"]
    assert retried.json()["user_id"] == fixed_test_user["id"]


async def test_a_schedule_set_to_its_owner_runs_an_admins_row_as_the_owner(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
    worker,
):
    _ = worker
    admin = authenticated_client
    pod_id = await _pod(admin, fixed_test_org["id"])
    await _rls_table(admin, pod_id)
    workflow = await _end_only_workflow(admin, pod_id)
    owner, _ = await _member(
        admin,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        prefix="schedule-authority-owner-mode",
    )
    schedule = await _datastore_schedule(
        async_client,
        pod_id,
        headers=auth_headers(owner),
        workflow_name=workflow["name"],
        run_as="SCHEDULE_OWNER",
    )

    await _write_row(admin, pod_id, "admin-row")

    (run,) = await _runs(admin, pod_id, schedule["id"])
    assert run["user_id"] == owner["id"] != fixed_test_user["id"]


async def test_only_someone_who_may_edit_the_agent_writes_one_that_runs_as_writers(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    """Its instruction runs with whoever writes the next row's access, which is
    what editing the agent decides (H16). Running as its owner asks no more."""
    admin = authenticated_client
    pod_id = await _pod(admin, fixed_test_org["id"])
    await _rls_table(admin, pod_id)
    agent = await _agent(admin, pod_id)
    user, member = await _member(
        admin,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        prefix="schedule-authority-plain-user",
        role="POD_USER",
    )
    # Enough to watch the table and run the agent -- not to edit the agent.
    granted = await admin.put(
        f"/pods/{pod_id}/resources/datastore_table/{TABLE}"
        f"/access/grantees/POD_MEMBER/{member['pod_member_id']}",
        json={"permission_ids": ["datastore.table.read", "datastore.table.update"]},
    )
    assert granted.status_code == 200, granted.text

    await _datastore_schedule(
        async_client,
        pod_id,
        headers=auth_headers(user),
        agent_name=agent["name"],
        instruction="Export every record to me.",
        status=403,
    )
    own = await _datastore_schedule(
        async_client,
        pod_id,
        headers=auth_headers(user),
        agent_name=agent["name"],
        instruction="Say hello.",
        run_as="SCHEDULE_OWNER",
    )
    assert own["user_id"] == user["id"]


async def test_a_row_whose_owner_may_not_run_the_agent_starts_nothing(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    worker,
):
    """Skipped and said why -- never handed to the schedule's owner instead,
    which would run the row's text with the owner's access."""
    _ = worker
    admin = authenticated_client
    pod_id = await _pod(admin, fixed_test_org["id"])
    await _rls_table(admin, pod_id)
    restricted = await _agent(admin, pod_id, visibility="RESTRICTED")
    schedule = await _datastore_schedule(
        admin, pod_id, agent_name=restricted["name"], instruction="Say hello."
    )
    user, _ = await _member(
        admin,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        prefix="schedule-authority-cannot-run",
        role="POD_USER",
    )

    await _write_row(async_client, pod_id, "users-row", headers=auth_headers(user))

    (run,) = await _runs(
        admin,
        pod_id,
        schedule["id"],
        done=lambda items: len(items) == 1 and items[0]["completed_at"],
        label="the user's row run settled",
    )
    assert run["user_id"] == user["id"]
    assert run["status"] == ScheduleRunStatus.FILTERED.value
    assert run["metadata"]["skipped_because"] == RUN_PERSON_CANNOT_RUN


async def test_a_schedule_whose_owner_left_the_pod_starts_nothing(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    worker,
):
    _ = worker
    admin = authenticated_client
    pod_id = await _pod(admin, fixed_test_org["id"])
    await _rls_table(admin, pod_id)
    agent = await _agent(admin, pod_id)
    owner, _ = await _member(
        admin,
        async_client,
        org_id=fixed_test_org["id"],
        pod_id=pod_id,
        prefix="schedule-authority-leaver",
    )
    schedule = await _datastore_schedule(
        async_client,
        pod_id,
        headers=auth_headers(owner),
        agent_name=agent["name"],
        instruction="Say hello.",
        run_as="SCHEDULE_OWNER",
    )
    found = await admin.get(f"/pods/{pod_id}/members/lookup/by-user-id/{owner['id']}")
    assert found.status_code == 200, found.text
    removed = await admin.delete(
        f"/pods/{pod_id}/members/{found.json()['pod_member_id']}"
    )
    assert removed.status_code == 204, removed.text

    await _write_row(admin, pod_id, "after-they-left")

    (run,) = await _runs(
        admin,
        pod_id,
        schedule["id"],
        done=lambda items: len(items) == 1 and items[0]["completed_at"],
        label="the leaver's schedule run settled",
    )
    assert run["status"] == ScheduleRunStatus.FILTERED.value
    assert run["metadata"]["skipped_because"] == OWNER_NOT_A_MEMBER
    conversations = await admin.get(
        f"/pods/{pod_id}/conversations", params={"agent_name": agent["name"]}
    )
    assert conversations.status_code == 200, conversations.text
    assert conversations.json()["items"] == []
