import asyncio
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.events.models import DomainEventOutbox
from app.modules.connectors.infrastructure.models.connector import Connector
from app.modules.connectors.infrastructure.models.connector_trigger import (
    ConnectorTrigger,
)
from app.modules.schedule.domain.schedule import ScheduleRunStatus, ScheduleType
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.test_support.e2e.waiters import eventually, wait_for_status
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e

SCHEDULE_E2E_TIMEOUT_SECONDS = 90


def _returns_async(fn):
    """Adapt a sync stub to the now-async ``WebhookVerifier.verify`` port.

    The port became a coroutine so implementations are forced to offload the
    blocking Composio SDK off the event loop. A sync stub still type-checks as
    a callable, so without this the route awaits a dict, raises, and answers
    403 -- which is what these tests started doing.
    """

    async def _call(*args, **kwargs):
        return fn(*args, **kwargs)

    return _call


async def _create_pod(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"Schedule Pod {uuid4().hex[:6]}",
            "description": "Schedule E2E pod",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _create_workflow(
    client: AsyncClient,
    pod_id: str,
    *,
    start: dict,
    name_prefix: str,
    nodes: list[dict] | None = None,
    edges: list[dict] | None = None,
) -> dict:
    create = await client.post(
        f"/pods/{pod_id}/workflows",
        json={
            "name": f"{name_prefix}-{uuid4().hex[:6]}",
            "start": start,
            "mode": "GLOBAL",
        },
    )
    assert create.status_code == 201, create.text
    workflow_name = create.json()["name"]

    graph = await client.put(
        f"/pods/{pod_id}/workflows/{workflow_name}/graph",
        json={
            "start": start,
            "nodes": nodes or [{"id": "end", "type": "END", "label": "Done"}],
            "edges": edges or [],
        },
    )
    assert graph.status_code == 200, graph.text
    return graph.json()


async def _create_agent(client: AsyncClient, pod_id: str) -> dict:
    response = await client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": f"scheduled_agent_{uuid4().hex[:6]}",
            "description": "Agent target for schedule e2e",
            "instruction": "Acknowledge scheduled payloads briefly.",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


async def _create_schedule(
    client: AsyncClient,
    pod_id: str,
    *,
    schedule_type: str,
    agent_name: str | None = None,
    workflow_name: str | None = None,
    config: dict,
    name: str | None = None,
    account_id: str | None = None,
    connector_trigger_id: str | None = None,
    filter_instruction: str | None = None,
    filter_output_schema: dict | None = None,
    visibility: str | None = None,
    headers: dict[str, str] | None = None,
    expected_status: int = 201,
) -> dict:
    payload = {
        "schedule_type": schedule_type,
        "config": config,
    }
    if name is not None:
        payload["name"] = name
    if agent_name is not None:
        payload["agent_name"] = agent_name
    if workflow_name is not None:
        payload["workflow_name"] = workflow_name
    if account_id is not None:
        payload["account_id"] = account_id
    if connector_trigger_id is not None:
        payload["connector_trigger_id"] = connector_trigger_id
    if filter_instruction is not None:
        payload["filter_instruction"] = filter_instruction
    if filter_output_schema is not None:
        payload["filter_output_schema"] = filter_output_schema
    if visibility is not None:
        payload["visibility"] = visibility
    response = await client.post(
        f"/pods/{pod_id}/schedules",
        json=payload,
        headers=headers,
    )
    assert response.status_code == expected_status, response.text
    return response.json() if response.content else {}


async def _seed_connector_trigger(
    db_session: AsyncSession,
    *,
    connector_id: str,
    trigger_id: str,
    event_type: str,
    config_schema: dict | None = None,
    payload_schema: dict | None = None,
) -> None:
    connector = await db_session.get(Connector, connector_id)
    if connector is None:
        db_session.add(
            Connector(
                id=connector_id,
                title=connector_id.replace("_", " ").title(),
                kinds=[{"kind": "package", "auth_scheme": "OAUTH2"}],
                is_active=True,
            )
        )

    trigger = await db_session.get(ConnectorTrigger, trigger_id)
    if trigger is None:
        db_session.add(
            ConnectorTrigger(
                id=trigger_id,
                connector_id=connector_id,
                event_type=event_type,
                description=f"{connector_id} {event_type}",
                config_schema=config_schema or {"type": "object"},
                payload_schema=payload_schema or {"type": "object"},
            )
        )
    else:
        trigger.connector_id = connector_id
        trigger.event_type = event_type
        trigger.config_schema = config_schema or {"type": "object"}
        trigger.payload_schema = payload_schema or {"type": "object"}
    await db_session.commit()


async def _seed_composio_webhook_trigger(db_session: AsyncSession) -> None:
    await _seed_connector_trigger(
        db_session,
        connector_id="composio",
        trigger_id="OUTLOOK_MESSAGE_TRIGGER",
        event_type="outlook.message",
        config_schema={"type": "object"},
        payload_schema={"type": "object"},
    )


async def _workflow_runs(
    client: AsyncClient, pod_id: str, workflow_name: str
) -> list[dict]:
    response = await client.get(f"/pods/{pod_id}/workflows/{workflow_name}/runs")
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _workflow_run(client: AsyncClient, pod_id: str, run_id: str) -> dict:
    response = await client.get(f"/pods/{pod_id}/workflow-runs/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def _wait_for_run_status(
    client: AsyncClient,
    pod_id: str,
    run_id: str,
    status: str,
) -> dict:
    async def probe() -> dict:
        return await _workflow_run(client, pod_id, run_id)

    # failed={"FAILED"} (not wait_for_status's default {"FAILED", "ERROR"}):
    # every caller here waits for "COMPLETED" -- "FAILED" is never the
    # requested target -- so this preserves the original's unconditional
    # fail-fast the instant the run's status becomes FAILED, regardless of
    # what `status` was asked for. ("ERROR" isn't a WorkflowRunStatus value,
    # so the two failed-sets are equivalent here; spelled out explicitly
    # rather than relying on that coincidence.)
    return await wait_for_status(
        label=f"workflow run {run_id} status {status}",
        probe=probe,
        expected={status},
        failed={"FAILED"},
        timeout_seconds=SCHEDULE_E2E_TIMEOUT_SECONDS,
        interval_seconds=0.15,
    )


async def _wait_for_workflow_run(
    client: AsyncClient,
    pod_id: str,
    workflow_name: str,
    *,
    source: str,
    timeout_seconds: float = SCHEDULE_E2E_TIMEOUT_SECONDS,
) -> dict:
    async def probe() -> dict:
        for run_summary in await _workflow_runs(client, pod_id, workflow_name):
            if run_summary["status"] not in {"COMPLETED", "FAILED"}:
                continue
            run = await _workflow_run(client, pod_id, run_summary["id"])
            if run_summary["status"] == "FAILED":
                return {"outcome": "FAILED", "run": run}
            start_payload = run.get("execution_context", {}).get("start", {})
            if start_payload.get("payload", {}).get("source") == source:
                return {"outcome": "COMPLETED", "run": run}
            if start_payload.get("payload", {}).get("data", {}).get("source") == source:
                return {"outcome": "COMPLETED", "run": run}
            # This terminal run didn't match `source` -- fall through to the
            # next run_summary in the same pass, same as the original.
        return {"outcome": "PENDING", "run": None}

    # status_field="outcome" is synthetic, folded in by probe() above (not
    # part of the API response) -- it lets a "first terminal run found" scan
    # over a whole list, with a full-detail fetch per candidate, reuse
    # wait_for_status's fail-fast/expected machinery instead of hand-rolling
    # it. failed={"FAILED"} preserves the original's immediate pytest.fail on
    # the first FAILED terminal run encountered while scanning, matching or
    # not.
    result = await wait_for_status(
        label=f"workflow run from {source}",
        probe=probe,
        status_field="outcome",
        expected={"COMPLETED"},
        failed={"FAILED"},
        timeout_seconds=timeout_seconds,
        interval_seconds=0.15,
    )
    return result["run"]


async def _wait_for_agent_conversation(
    client: AsyncClient,
    pod_id: str,
    agent_name: str,
    *,
    timeout_seconds: float = SCHEDULE_E2E_TIMEOUT_SECONDS,
) -> dict:
    async def probe() -> list[dict]:
        response = await client.get(
            f"/pods/{pod_id}/conversations",
            params={"agent_name": agent_name, "limit": 10},
        )
        assert response.status_code == 200, response.text
        return response.json()["items"]

    # No fail-fast concept in the original -- just poll until a conversation
    # shows up or time runs out.
    items = await eventually(
        label=f"scheduled agent conversation {agent_name}",
        probe=probe,
        done=bool,
        timeout_seconds=timeout_seconds,
        interval_seconds=0.15,
    )
    return items[0]


async def _wait_for_schedule_runs(
    client: AsyncClient,
    pod_id: str,
    schedule_id: str,
    *,
    count: int,
    status: str,
) -> list[dict]:
    async def probe() -> list[dict]:
        response = await client.get(f"/pods/{pod_id}/schedules/{schedule_id}/runs")
        assert response.status_code == 200, response.text
        return response.json()["items"]

    # No fail-fast: `status` here is legitimately "TARGET_FAILED" at call
    # sites, so this must never treat a FAILED-shaped status as an error.
    return await eventually(
        label=f"{count} schedule runs with status {status}",
        probe=probe,
        done=lambda items: (
            len(items) == count and all(item["status"] == status for item in items)
        ),
        timeout_seconds=SCHEDULE_E2E_TIMEOUT_SECONDS,
        interval_seconds=0.15,
    )


async def _wait_for_schedule_run_status_counts(
    client: AsyncClient,
    pod_id: str,
    schedule_id: str,
    expected: dict[str, int],
) -> list[dict]:
    expected_total = sum(expected.values())

    async def probe() -> list[dict]:
        response = await client.get(f"/pods/{pod_id}/schedules/{schedule_id}/runs")
        assert response.status_code == 200, response.text
        return response.json()["items"]

    def _matches(items: list[dict]) -> bool:
        actual = {
            status: sum(item["status"] == status for item in items)
            for status in expected
        }
        return len(items) == expected_total and actual == expected

    # No fail-fast: `expected` legitimately includes "TARGET_FAILED"/"FAILED"
    # counts at call sites -- those are the awaited distribution, not errors.
    return await eventually(
        label=f"schedule run statuses {expected}",
        probe=probe,
        done=_matches,
        timeout_seconds=SCHEDULE_E2E_TIMEOUT_SECONDS,
        interval_seconds=0.15,
    )


def _composio_log_payload() -> dict:
    provider_id = f"composio-trigger-{uuid4().hex[:8]}"
    return {
        "id": f"composio-log-{uuid4().hex[:8]}",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": "OUTLOOK_MESSAGE_TRIGGER",
        "metadata": {
            "log_id": f"log-{uuid4().hex[:8]}",
            "trigger_id": provider_id,
            "connected_account_id": f"connected-{uuid4().hex[:8]}",
            "auth_config_id": f"auth-config-{uuid4().hex[:8]}",
            "user_id": f"composio-user-{uuid4().hex[:8]}",
            "toolkit_slug": "outlook",
        },
        "data": {
            "source": "composio",
            "subject": "Schedule webhook e2e",
        },
    }


async def _create_datastore_table(
    client: AsyncClient,
    pod_id: str,
    table_name: str = "schedule_records",
) -> None:
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "table_name": table_name,
            "primary_key_column": "id",
            "columns": [
                {"name": "source", "type": "TEXT"},
                {"name": "value", "type": "TEXT"},
            ],
            "enable_rls": True,
        },
    )
    assert response.status_code in {200, 201}, response.text


@pytest.mark.asyncio
async def test_schedule_list_and_get_honor_visibility_and_resource_grants(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    scheduled_at = (datetime.now(timezone.utc) + timedelta(minutes=30)).isoformat()
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={"type": "SCHEDULED", "config": {"schedule_type": "ONCE"}},
        name_prefix=f"personal_schedule_workflow_{uuid4().hex[:8]}",
    )
    restricted_workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={"type": "SCHEDULED", "config": {"schedule_type": "ONCE"}},
        name_prefix=f"restricted_schedule_workflow_{uuid4().hex[:8]}",
    )

    pod_user = await signup_user(async_client, "schedule-list-pod-user")
    editor = await signup_user(async_client, "schedule-list-editor")
    pod_user_org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=pod_user,
    )
    editor_org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=editor,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=pod_user_org_member["id"],
        role="POD_USER",
        roles=["POD_USER"],
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=editor_org_member["id"],
        role="POD_EDITOR",
        roles=["POD_EDITOR"],
    )
    pod_user_headers = auth_headers(pod_user)
    editor_headers = auth_headers(editor)

    owner_schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        workflow_name=workflow["name"],
        config={"scheduled_at": scheduled_at, "payload": {"source": "owner"}},
    )
    restricted_schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        workflow_name=restricted_workflow["name"],
        config={"scheduled_at": scheduled_at, "payload": {"source": "restricted"}},
        visibility="RESTRICTED",
    )
    editor_schedule = await _create_schedule(
        async_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        workflow_name=workflow["name"],
        config={"scheduled_at": scheduled_at, "payload": {"source": "editor"}},
        headers=editor_headers,
    )

    owner_list = await authenticated_client.get(f"/pods/{pod_id}/schedules")
    assert owner_list.status_code == 200, owner_list.text
    assert {item["id"] for item in owner_list.json()["items"]} == {
        owner_schedule["id"],
        restricted_schedule["id"],
        editor_schedule["id"],
    }

    pod_user_list = await async_client.get(
        f"/pods/{pod_id}/schedules",
        headers=pod_user_headers,
    )
    assert pod_user_list.status_code == 200, pod_user_list.text
    assert {item["id"] for item in pod_user_list.json()["items"]} == {
        owner_schedule["id"],
        editor_schedule["id"],
    }

    editor_list = await async_client.get(
        f"/pods/{pod_id}/schedules",
        headers=editor_headers,
    )
    assert editor_list.status_code == 200, editor_list.text
    assert {item["id"] for item in editor_list.json()["items"]} == {
        owner_schedule["id"],
        editor_schedule["id"],
    }
    editor_items = {item["id"]: item for item in editor_list.json()["items"]}
    assert set(editor_items[owner_schedule["id"]]["allowed_actions"]) == {
        "schedule.read",
        "schedule.update",
    }
    assert set(editor_items[editor_schedule["id"]]["allowed_actions"]) == {
        "schedule.read",
        "schedule.update",
        "schedule.delete",
    }
    editor_get_owner = await async_client.get(
        f"/pods/{pod_id}/schedules/{owner_schedule['id']}",
        headers=editor_headers,
    )
    assert editor_get_owner.status_code == 200, editor_get_owner.text
    assert set(editor_get_owner.json()["allowed_actions"]) == {
        "schedule.read",
        "schedule.update",
    }
    editor_get_editor = await async_client.get(
        f"/pods/{pod_id}/schedules/{editor_schedule['id']}",
        headers=editor_headers,
    )
    assert editor_get_editor.status_code == 200, editor_get_editor.text
    assert set(editor_get_editor.json()["allowed_actions"]) == {
        "schedule.read",
        "schedule.update",
        "schedule.delete",
    }

    get_before_grant = await async_client.get(
        f"/pods/{pod_id}/schedules/{restricted_schedule['id']}",
        headers=pod_user_headers,
    )
    assert get_before_grant.status_code == 403

    grant = await authenticated_client.put(
        f"/pods/{pod_id}/roles/POD_USER/permissions",
        json={
            "grants": [
                {
                    "resource_type": "schedule",
                    "resource_name": restricted_schedule["name"],
                    "permission_ids": ["schedule.read"],
                }
            ]
        },
    )
    assert grant.status_code == 200, grant.text

    get_after_grant = await async_client.get(
        f"/pods/{pod_id}/schedules/{restricted_schedule['id']}",
        headers=pod_user_headers,
    )
    assert get_after_grant.status_code == 200, get_after_grant.text
    assert set(get_after_grant.json()["allowed_actions"]) == {"schedule.read"}

    pod_user_list_after_grant = await async_client.get(
        f"/pods/{pod_id}/schedules",
        headers=pod_user_headers,
    )
    assert pod_user_list_after_grant.status_code == 200, pod_user_list_after_grant.text
    assert {item["id"] for item in pod_user_list_after_grant.json()["items"]} == {
        owner_schedule["id"],
        restricted_schedule["id"],
        editor_schedule["id"],
    }
    pod_user_items = {
        item["id"]: item for item in pod_user_list_after_grant.json()["items"]
    }
    assert set(pod_user_items[owner_schedule["id"]]["allowed_actions"]) == {
        "schedule.read"
    }
    assert set(pod_user_items[restricted_schedule["id"]]["allowed_actions"]) == {
        "schedule.read"
    }
    assert set(pod_user_items[editor_schedule["id"]]["allowed_actions"]) == {
        "schedule.read"
    }


@pytest.mark.asyncio
async def test_pod_users_create_and_manage_their_own_personal_schedules(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={"type": "SCHEDULED", "config": {"schedule_type": "ONCE"}},
        name_prefix="installable-workflow",
    )
    agent = await _create_agent(authenticated_client, pod_id)

    async def _add_member(slug: str, *, role: str) -> tuple[dict, dict[str, str]]:
        user = await signup_user(async_client, slug)
        org_member = await invite_org_member(
            authenticated_client,
            async_client,
            org_id=fixed_test_org["id"],
            user=user,
        )
        await add_pod_member(
            authenticated_client,
            pod_id=pod_id,
            organization_member_id=org_member["id"],
            role=role,
            roles=[role],
        )
        return user, auth_headers(user)

    pod_user, pod_user_headers = await _add_member("schedule-pod-user", role="POD_USER")
    # A second pod user, to prove personal schedules stay private to their owner.
    peer_user, peer_headers = await _add_member("schedule-peer-user", role="POD_USER")
    # A pod viewer lacks schedule.create entirely.
    viewer, viewer_headers = await _add_member("schedule-viewer", role="POD_VIEWER")

    # Pod users are not editors: they still cannot author agents.
    blocked_agent = await async_client.post(
        f"/pods/{pod_id}/agents",
        headers=pod_user_headers,
        json={
            "name": f"blocked_agent_{uuid4().hex[:6]}",
            "description": "Pod users should not create agents",
            "instruction": "No-op",
            "input_schema": {"type": "object"},
            "output_schema": {"type": "object"},
        },
    )
    assert blocked_agent.status_code == 403, blocked_agent.text

    # A pod viewer cannot create schedules (no schedule.create).
    await _create_schedule(
        async_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        agent_name=agent["name"],
        config={
            "scheduled_at": (
                datetime.now(timezone.utc) + timedelta(minutes=30)
            ).isoformat(),
            "payload": {"source": "viewer"},
        },
        headers=viewer_headers,
        expected_status=403,
    )

    # A pod user creates an agent schedule: owned by them, PERSONAL, fully theirs.
    agent_schedule = await _create_schedule(
        async_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        agent_name=agent["name"],
        config={
            "scheduled_at": (
                datetime.now(timezone.utc) + timedelta(minutes=31)
            ).isoformat(),
            "payload": {"source": "pod-user-agent"},
        },
        headers=pod_user_headers,
    )
    assert agent_schedule["user_id"] == pod_user["id"]
    assert agent_schedule["visibility"] == "PERSONAL"
    assert set(agent_schedule["allowed_actions"]) == {
        "schedule.read",
        "schedule.update",
        "schedule.delete",
    }

    # A schedule targeting a GLOBAL workflow is a pod-wide trigger: it stays POD.
    workflow_schedule = await _create_schedule(
        async_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        workflow_name=workflow["name"],
        config={
            "scheduled_at": (
                datetime.now(timezone.utc) + timedelta(minutes=32)
            ).isoformat(),
            "payload": {"source": "pod-user-workflow"},
        },
        headers=pod_user_headers,
    )
    assert workflow_schedule["user_id"] == pod_user["id"]
    assert workflow_schedule["visibility"] == "POD"

    # The owner reads and updates their own personal schedule.
    owner_get = await async_client.get(
        f"/pods/{pod_id}/schedules/{agent_schedule['id']}",
        headers=pod_user_headers,
    )
    assert owner_get.status_code == 200, owner_get.text
    owner_update = await async_client.patch(
        f"/pods/{pod_id}/schedules/{agent_schedule['id']}",
        headers=pod_user_headers,
        json={"is_active": False},
    )
    assert owner_update.status_code == 200, owner_update.text
    assert owner_update.json()["is_active"] is False

    # A peer pod user cannot see the personal schedule in their listing, but the
    # GLOBAL-workflow schedule is pod-visible to everyone.
    peer_list = await async_client.get(
        f"/pods/{pod_id}/schedules", headers=peer_headers
    )
    assert peer_list.status_code == 200, peer_list.text
    peer_visible_ids = {item["id"] for item in peer_list.json()["items"]}
    assert agent_schedule["id"] not in peer_visible_ids
    assert workflow_schedule["id"] in peer_visible_ids

    # ...and the peer cannot read, update, or delete it directly.
    peer_get = await async_client.get(
        f"/pods/{pod_id}/schedules/{agent_schedule['id']}",
        headers=peer_headers,
    )
    assert peer_get.status_code == 403, peer_get.text
    peer_update = await async_client.patch(
        f"/pods/{pod_id}/schedules/{agent_schedule['id']}",
        headers=peer_headers,
        json={"is_active": True},
    )
    assert peer_update.status_code == 403, peer_update.text
    peer_delete = await async_client.delete(
        f"/pods/{pod_id}/schedules/{agent_schedule['id']}",
        headers=peer_headers,
    )
    assert peer_delete.status_code == 403, peer_delete.text

    # The owner deletes their own schedule.
    owner_delete = await async_client.delete(
        f"/pods/{pod_id}/schedules/{agent_schedule['id']}",
        headers=pod_user_headers,
    )
    assert owner_delete.status_code == 204, owner_delete.text


@pytest.mark.asyncio
async def test_time_schedules_start_workflow_and_agent_targets(
    authenticated_client: AsyncClient,
    fixed_test_org,
    worker,
):
    _ = worker
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={"type": "SCHEDULED", "config": {"schedule_type": "ONCE"}},
        name_prefix="time-workflow",
    )
    agent = await _create_agent(authenticated_client, pod_id)

    scheduled_at = (datetime.now(timezone.utc) + timedelta(seconds=5)).isoformat()
    workflow_schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        workflow_name=workflow["name"],
        config={"scheduled_at": scheduled_at, "payload": {"source": "time-workflow"}},
    )
    assert workflow_schedule["workflow_id"] == workflow["id"]
    assert workflow_schedule["workflow_name"] == workflow["name"]

    duplicate = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        workflow_name=workflow["name"],
        config={
            "scheduled_at": (
                datetime.now(timezone.utc) + timedelta(minutes=1)
            ).isoformat(),
            "payload": {"source": "duplicate"},
        },
        expected_status=422,
    )
    assert "already has a schedule" in duplicate["message"]

    await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        agent_name=agent["name"],
        config={
            "scheduled_at": (
                datetime.now(timezone.utc) + timedelta(seconds=6)
            ).isoformat(),
            "payload": {"source": "time-agent"},
        },
    )

    run = await _wait_for_workflow_run(
        authenticated_client,
        pod_id,
        workflow["name"],
        source="time-workflow",
    )
    assert run["start_type"] == "SCHEDULED"
    conversation = await _wait_for_agent_conversation(
        authenticated_client,
        pod_id,
        agent["name"],
    )
    assert conversation["agent_id"] == agent["id"]


@pytest.mark.asyncio
async def test_cron_schedule_fires_workflow(
    authenticated_client: AsyncClient,
    fixed_test_org,
    worker,
    db_session: AsyncSession,
    monkeypatch,
):
    """A recurring CRON schedule fires its workflow live via the scheduler."""
    _ = worker
    # The product floor is 15 minutes, which no test can wait out. Lower it so a
    # once-a-minute cron is accepted at all; the floor itself is covered by
    # test_time_schedule_policy.
    monkeypatch.setattr(
        "app.modules.schedule.services.time_schedule_policy."
        "schedule_settings.schedule_minimum_interval_minutes",
        1,
    )
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={"type": "SCHEDULED", "config": {"schedule_type": "CRON"}},
        name_prefix="cron-workflow",
    )
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        workflow_name=workflow["name"],
        config={"cron": "* * * * *", "payload": {"source": "cron-workflow"}},
    )
    assert schedule["config"]["cron"] == "* * * * *"

    # This used to wait for the real next minute boundary, which cost up to 60
    # seconds of doing nothing and made it the third-slowest test in the whole
    # e2e suite. What is under test is that the poller claims a due cron row and
    # fires its workflow -- not that wall-clock advances. So make the occurrence
    # due now: claim_due_schedules selects on `next_fire_at <= now()`, and the
    # poller ticks every DEFAULT_POLL_INTERVAL_SECONDS.
    row = await db_session.get(Schedule, UUID(schedule["id"]))
    assert row is not None, "the schedule API returned an id with no row behind it"
    row.next_fire_at = datetime.now(timezone.utc) - timedelta(seconds=1)
    await db_session.commit()

    run = await _wait_for_workflow_run(
        authenticated_client,
        pod_id,
        workflow["name"],
        source="cron-workflow",
        timeout_seconds=60,
    )
    assert run["start_type"] == "SCHEDULED"


@pytest.mark.asyncio
async def test_wait_until_resumes_through_scheduler_with_exact_wait_ref(
    authenticated_client: AsyncClient,
    fixed_test_org,
    worker,
):
    _ = worker
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={"type": "MANUAL"},
        name_prefix="wait-until-workflow",
        nodes=[
            {
                "id": "timer",
                "type": "WAIT_UNTIL",
                "config": {"timeout_seconds": 2},
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "timer-end", "source": "timer", "target": "end"}],
    )

    response = await authenticated_client.post(
        f"/pods/{pod_id}/workflows/{workflow['name']}/runs"
    )
    assert response.status_code == 201, response.text
    waiting = response.json()
    assert waiting["active_wait"]["wait_type"] == "TIME"
    wait_ref = waiting["active_wait"]["external_ref"]
    assert UUID(wait_ref) != UUID(waiting["id"])

    completed = await _wait_for_run_status(
        authenticated_client,
        pod_id,
        waiting["id"],
        "COMPLETED",
    )
    assert completed["active_wait"] is None
    assert [step["node_id"] for step in completed["step_history"]] == [
        "timer",
        "end",
    ]


@pytest.mark.asyncio
async def test_wait_until_resumes_from_a_timer_that_carries_no_owner(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
    worker,
):
    """A fired wait timer names no user, and the run still resumes.

    This used to be about legacy APScheduler job rows: jobs written by an older
    build deserialized with kwargs carrying no ``user_id``, and the test proved
    those still woke their run. The job store is gone, and the case it guarded
    is now the *only* case -- `claim_due_workflow_waits` builds its payload from
    the wait row's typed columns (`run_id`, `external_ref`, `scheduled_at`) and
    passes `user_id=None` for every timer it claims, because a timer is not a
    thing a user is doing.

    So the ownership has to come from the run row on the resume path, for every
    workflow wait rather than for old ones. This drives it end to end: the wait
    row is aged into the past, and the real poller inside the worker claims,
    dispatches, and resumes it.
    """
    from sqlalchemy import update

    from app.modules.workflow.infrastructure.models import WorkflowRunWaitModel

    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={"type": "MANUAL"},
        name_prefix="ownerless-timer-workflow",
        nodes=[
            {
                "id": "timer",
                "type": "WAIT_UNTIL",
                # Long enough that only the ageing below can resume this run.
                "config": {"timeout_seconds": 3600},
            },
            {"id": "end", "type": "END"},
        ],
        edges=[{"id": "timer-end", "source": "timer", "target": "end"}],
    )

    response = await authenticated_client.post(
        f"/pods/{pod_id}/workflows/{workflow['name']}/runs"
    )
    assert response.status_code == 201, response.text
    waiting = response.json()
    assert waiting["active_wait"]["wait_type"] == "TIME"
    wait_ref = waiting["active_wait"]["external_ref"]

    # Make it due. The poller's claim query is the thing under test from here:
    # nothing else knows this wait exists.
    await db_session.execute(
        update(WorkflowRunWaitModel)
        .where(WorkflowRunWaitModel.external_ref == wait_ref)
        .values(scheduled_at=datetime.now(timezone.utc) - timedelta(seconds=1))
    )
    await db_session.commit()

    completed = await _wait_for_run_status(
        authenticated_client,
        pod_id,
        waiting["id"],
        "COMPLETED",
    )
    assert completed["active_wait"] is None
    assert [step["node_id"] for step in completed["step_history"]] == ["timer", "end"]


@pytest.mark.asyncio
async def test_composio_webhook_schedule_starts_event_workflow_from_logged_payload(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
    monkeypatch,
    worker,
):
    _ = worker
    await _seed_composio_webhook_trigger(db_session)
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "EVENT",
            "config": {
                "connector_id": "composio",
                "connector_trigger_id": "OUTLOOK_MESSAGE_TRIGGER",
                "trigger_config": {"source": "composio"},
            },
        },
        name_prefix="webhook-workflow",
    )
    payload = _composio_log_payload()
    provider_id = payload["metadata"]["trigger_id"]
    await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.WEBHOOK.value,
        workflow_name=workflow["name"],
        config={"source": "composio", "provider_trigger_id": provider_id},
    )

    monkeypatch.setattr(
        "app.composition.schedule_connectors.ComposioWebhookVerifier.verify",
        _returns_async(
            lambda self, payload_text, headers: {
            "version": "V3",
            "payload": {
                "id": provider_id,
                "user_id": payload["metadata"]["user_id"],
                "toolkit_slug": payload["metadata"]["toolkit_slug"],
                "trigger_slug": payload["type"],
                "metadata": {
                    "connected_account": {
                        "id": payload["metadata"]["connected_account_id"],
                        "auth_config_id": payload["metadata"]["auth_config_id"],
                    }
                },
                "payload": {**payload["data"], "source": "composio-log"},
            },
            "raw_payload": payload,
        }
        ),
    )
    webhook = await authenticated_client.post("/webhooks/composio", json=payload)
    assert webhook.status_code == 200, webhook.text

    run = await _wait_for_workflow_run(
        authenticated_client,
        pod_id,
        workflow["name"],
        source="composio-log",
    )
    assert run["start_type"] == "EVENT"
    assert run["execution_context"]["start"]["metadata"]["provider_id"] == provider_id


@pytest.mark.asyncio
async def test_webhook_workflow_schedule_contract_derives_start_config(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    connector_id = f"gmail_{uuid4().hex[:8]}"
    connector_trigger_id = f"{connector_id}:gmail_new_gmail_message"
    await _seed_connector_trigger(
        db_session,
        connector_id=connector_id,
        trigger_id=connector_trigger_id,
        event_type="gmail.new_message",
        config_schema={
            "type": "object",
            "properties": {"labelIds": {"type": "string"}},
        },
        payload_schema={
            "type": "object",
            "properties": {"subject": {"type": "string"}},
        },
    )
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "EVENT",
            "config": {
                "connector_id": connector_id,
                "connector_trigger_id": connector_trigger_id,
                "trigger_config": {"labelIds": "INBOX"},
            },
        },
        name_prefix="webhook-contract-workflow",
    )

    explicit_trigger = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.WEBHOOK.value,
        workflow_name=workflow["name"],
        connector_trigger_id=connector_trigger_id,
        config={"source": "composio"},
        expected_status=422,
    )
    assert "connector_trigger_id" in explicit_trigger["details"][0]["msg"]

    conflicting_config = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.WEBHOOK.value,
        workflow_name=workflow["name"],
        config={"source": "composio", "labelIds": "SENT"},
        expected_status=422,
    )
    assert "trigger_config" in conflicting_config["message"]

    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.WEBHOOK.value,
        workflow_name=workflow["name"],
        config={"source": "composio"},
        filter_instruction="Only continue for relevant email",
        filter_output_schema={"type": "object"},
    )
    assert schedule["workflow_id"] == workflow["id"]
    assert schedule["agent_id"] is None
    assert schedule["connector_trigger_id"] == connector_trigger_id
    assert schedule["config"] == {"source": "composio", "labelIds": "INBOX"}
    assert schedule["filter_instruction"] == "Only continue for relevant email"
    assert schedule["filter_output_schema"] == {"type": "object"}


@pytest.mark.asyncio
async def test_webhook_workflow_schedule_requires_event_start(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={"type": "MANUAL"},
        name_prefix="manual-webhook-workflow",
    )

    response = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.WEBHOOK.value,
        workflow_name=workflow["name"],
        config={"source": "composio"},
        expected_status=422,
    )
    assert "EVENT workflow start" in response["message"]


@pytest.mark.asyncio
async def test_webhook_agent_schedule_contract_requires_connector_trigger(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    connector_id = f"slack_{uuid4().hex[:8]}"
    connector_trigger_id = f"{connector_id}:message_created"
    await _seed_connector_trigger(
        db_session,
        connector_id=connector_id,
        trigger_id=connector_trigger_id,
        event_type="message.created",
        config_schema={
            "type": "object",
            "properties": {"channel_id": {"type": "string"}},
        },
        payload_schema={"type": "object"},
    )

    missing_trigger = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.WEBHOOK.value,
        agent_name=agent["name"],
        config={"source": "slack", "channel_id": "C123"},
        expected_status=422,
    )
    assert "connector_trigger_id" in missing_trigger["details"][0]["msg"]

    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.WEBHOOK.value,
        agent_name=agent["name"],
        connector_trigger_id=connector_trigger_id,
        config={"source": "slack", "channel_id": "C123"},
        filter_instruction="Only continue for urgent messages",
        filter_output_schema={"type": "object"},
    )
    assert schedule["agent_id"] == agent["id"]
    assert schedule["workflow_id"] is None
    assert schedule["connector_trigger_id"] == connector_trigger_id
    assert schedule["config"] == {"source": "slack", "channel_id": "C123"}
    assert schedule["filter_instruction"] == "Only continue for urgent messages"
    assert schedule["filter_output_schema"] == {"type": "object"}


@pytest.mark.asyncio
async def test_datastore_schedule_starts_workflow_from_record_api(
    authenticated_client: AsyncClient,
    fixed_test_org,
    worker,
):
    _ = worker
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _create_datastore_table(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "schedule_records", "operations": ["INSERT"]},
        },
        name_prefix="datastore-workflow",
    )
    await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config={"table_name": "schedule_records", "operations": ["INSERT"]},
    )

    record = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables/schedule_records/records",
        json={"data": {"source": "datastore-record", "value": "hello"}},
    )
    assert record.status_code == 201, record.text

    run = await _wait_for_workflow_run(
        authenticated_client,
        pod_id,
        workflow["name"],
        source="datastore-record",
    )
    assert run["start_type"] == "DATASTORE_EVENT"


@pytest.mark.asyncio
async def test_rls_datastore_schedule_run_and_workflow_belong_to_row_owner(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
    db_manager,
    worker,
):
    _ = worker
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _create_datastore_table(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "schedule_records", "operations": ["INSERT"]},
        },
        name_prefix="row-owner-workflow",
    )
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config={"table_name": "schedule_records", "operations": ["INSERT"]},
    )
    row_owner = await signup_user(async_client, "schedule-row-owner")
    org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=row_owner,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role="POD_USER",
        roles=["POD_USER"],
    )

    record = await async_client.post(
        f"/pods/{pod_id}/datastore/tables/schedule_records/records",
        headers=auth_headers(row_owner),
        json={"data": {"source": "row-owner-record", "value": "hello"}},
    )
    assert record.status_code == 201, record.text

    workflow_run = await _wait_for_workflow_run(
        authenticated_client,
        pod_id,
        workflow["name"],
        source="row-owner-record",
    )
    assert workflow_run["user_id"] == row_owner["id"]

    schedule_runs = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule['id']}/runs"
    )
    assert schedule_runs.status_code == 200, schedule_runs.text
    run_items = schedule_runs.json()["items"]
    assert len(run_items) == 1
    assert run_items[0]["user_id"] == row_owner["id"]
    assert run_items[0]["target_run_id"] == workflow_run["id"]

    owner_runs = await async_client.get(
        f"/pods/{pod_id}/schedules/{schedule['id']}/runs",
        headers=auth_headers(row_owner),
    )
    assert owner_runs.status_code == 200, owner_runs.text
    assert [item["user_id"] for item in owner_runs.json()["items"]] == [row_owner["id"]]

    peer = await signup_user(async_client, "schedule-run-history-peer")
    peer_org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=peer,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=peer_org_member["id"],
        role="POD_EDITOR",
        roles=["POD_EDITOR"],
    )
    peer_runs = await async_client.get(
        f"/pods/{pod_id}/schedules/{schedule['id']}/runs",
        headers=auth_headers(peer),
    )
    assert peer_runs.status_code == 200, peer_runs.text
    assert peer_runs.json()["items"] == []

    source_run_id = UUID(run_items[0]["id"])
    async with db_manager.session_factory() as session, session.begin():
        source_run = await session.get(ScheduleRun, source_run_id)
        assert source_run is not None
        source_run.target_outcome = ScheduleRunStatus.TARGET_FAILED.value
        source_run.completed_at = datetime.now(timezone.utc)

    retry_path = (
        f"/pods/{pod_id}/schedules/{schedule['id']}/runs/{source_run_id}/retry"
    )
    hidden_retry = await async_client.post(
        retry_path,
        headers=auth_headers(peer),
    )
    assert hidden_retry.status_code == 409, hidden_retry.text
    assert hidden_retry.json()["code"] == "SCHEDULE_RUN_NOT_RETRYABLE"

    admin_retry = await authenticated_client.post(retry_path)
    assert admin_retry.status_code == 202, admin_retry.text


@pytest.mark.asyncio
async def test_five_row_owner_workflow_failures_deactivate_schedule_owner_schedule(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_user,
    fixed_test_org,
    db_manager,
    e2e_settings,
    worker,
):
    _ = worker
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _create_datastore_table(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "schedule_records", "operations": ["INSERT"]},
        },
        name_prefix="breaker-row-owner-workflow",
        nodes=[
            {
                "id": "call_missing_agent",
                "type": "AGENT",
                "label": "Fail deterministically",
                "config": {
                    "agent_name": "does-not-exist",
                    "input_mapping": {},
                },
            },
            {"id": "end", "type": "END", "label": "Done"},
        ],
        edges=[
            {
                "id": "missing-agent-end",
                "source": "call_missing_agent",
                "target": "end",
            }
        ],
    )
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config={"table_name": "schedule_records", "operations": ["INSERT"]},
    )
    row_owner = await signup_user(async_client, "schedule-breaker-row-owner")
    org_member = await invite_org_member(
        authenticated_client,
        async_client,
        org_id=fixed_test_org["id"],
        user=row_owner,
    )
    await add_pod_member(
        authenticated_client,
        pod_id=pod_id,
        organization_member_id=org_member["id"],
        role="POD_USER",
        roles=["POD_USER"],
    )

    for index in range(5):
        record = await async_client.post(
            f"/pods/{pod_id}/datastore/tables/schedule_records/records",
            headers=auth_headers(row_owner),
            json={
                "data": {
                    "source": f"breaker-row-{index}",
                    "value": "fail target workflow",
                }
            },
        )
        assert record.status_code == 201, record.text
        await _wait_for_schedule_runs(
            authenticated_client,
            pod_id,
            schedule["id"],
            count=index + 1,
            status="TARGET_FAILED",
        )

    async with db_manager.session_factory() as session:
        persisted = await session.get(Schedule, UUID(schedule["id"]))
        assert persisted is not None
        assert persisted.is_active is False
        assert persisted.consecutive_failures == 5

        events = (
            await session.scalars(
                select(DomainEventOutbox).where(
                    DomainEventOutbox.event_type == "schedule.deactivated"
                )
            )
        ).all()
        matching_events = [
            event
            for event in events
            if event.payload.get("schedule_id") == schedule["id"]
        ]
        assert len(matching_events) == 1
        assert matching_events[0].payload["user_id"] == schedule["user_id"]

    async def _matching_emails() -> list[dict]:
        matches = []
        for path in Path(e2e_settings.email_output_dir).glob("*.json"):
            # The filesystem mail spool is written by a separate worker
            # process: ``glob`` can list a file whose ``os.open(O_CREAT)``
            # has landed but whose ``json.dump`` body hasn't been flushed
            # yet, or one the retention sweep deleted between the listing
            # and the read. Either shows up here as an empty/partial read,
            # not a real message -- skip it and let the next poll pick up
            # the finished file, the same guard identity's e2e mailbox
            # helper (``_filesystem_emails``) already applies.
            try:
                message = json.loads(path.read_text(encoding="utf-8"))
            except OSError, json.JSONDecodeError:
                continue
            # The subject leads with the schedule's humanized display name, so
            # match the stable tail rather than pinning the whole string.
            if message.get("to_email") == fixed_test_user["email"] and str(
                message.get("subject", "")
            ).endswith("was paused after repeated failures"):
                matches.append(message)
        return matches

    # No fail-fast -- done as soon as at least one match appears. The
    # exactness check (exactly one, not a duplicate send) stays a separate
    # assertion below, same as the original: it still catches 2+ matches,
    # and 0 matches now surfaces as eventually's own timeout failure instead
    # of via this assert.
    matching_emails = await eventually(
        label="pause email for the row owner's schedule",
        probe=_matching_emails,
        done=bool,
        timeout_seconds=SCHEDULE_E2E_TIMEOUT_SECONDS,
        interval_seconds=0.15,
    )
    assert len(matching_emails) == 1

    workflow_runs = await _workflow_runs(authenticated_client, pod_id, workflow["name"])
    assert len(workflow_runs) == 5
    assert {run["status"] for run in workflow_runs} == {"FAILED"}
    assert {run["user_id"] for run in workflow_runs} == {row_owner["id"]}

    sixth = await async_client.post(
        f"/pods/{pod_id}/datastore/tables/schedule_records/records",
        headers=auth_headers(row_owner),
        json={
            "data": {
                "source": "breaker-row-sixth",
                "value": "must not launch",
            }
        },
    )
    assert sixth.status_code == 201, sixth.text
    await asyncio.sleep(2)

    schedule_runs = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule['id']}/runs"
    )
    assert schedule_runs.status_code == 200, schedule_runs.text
    assert len(schedule_runs.json()["items"]) == 5
    assert (
        len(await _workflow_runs(authenticated_client, pod_id, workflow["name"])) == 5
    )


@pytest.mark.asyncio
async def test_successful_target_resets_partial_failure_streak(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_manager,
    worker,
):
    _ = worker
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _create_datastore_table(authenticated_client, pod_id)
    start = {
        "type": "DATASTORE_EVENT",
        "config": {"table_name": "schedule_records", "operations": ["INSERT"]},
    }
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start=start,
        name_prefix="breaker-reset-workflow",
        nodes=[
            {
                "id": "call_missing_agent",
                "type": "AGENT",
                "config": {
                    "agent_name": "does-not-exist",
                    "input_mapping": {},
                },
            },
            {"id": "end", "type": "END"},
        ],
        edges=[
            {
                "id": "missing-agent-end",
                "source": "call_missing_agent",
                "target": "end",
            }
        ],
    )
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config={"table_name": "schedule_records", "operations": ["INSERT"]},
    )

    for index in range(2):
        record = await authenticated_client.post(
            f"/pods/{pod_id}/datastore/tables/schedule_records/records",
            json={
                "data": {
                    "source": f"partial-failure-{index}",
                    "value": "fail target workflow",
                }
            },
        )
        assert record.status_code == 201, record.text
        await _wait_for_schedule_runs(
            authenticated_client,
            pod_id,
            schedule["id"],
            count=index + 1,
            status="TARGET_FAILED",
        )

    graph = await authenticated_client.put(
        f"/pods/{pod_id}/workflows/{workflow['name']}/graph",
        json={
            "start": start,
            "nodes": [{"id": "end", "type": "END", "label": "Done"}],
            "edges": [],
        },
    )
    assert graph.status_code == 200, graph.text

    success = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables/schedule_records/records",
        json={
            "data": {
                "source": "breaker-reset-success",
                "value": "complete target workflow",
            }
        },
    )
    assert success.status_code == 201, success.text
    await _wait_for_schedule_run_status_counts(
        authenticated_client,
        pod_id,
        schedule["id"],
        {"TARGET_FAILED": 2, "COMPLETED": 1},
    )

    async with db_manager.session_factory() as session:
        persisted = await session.get(Schedule, UUID(schedule["id"]))
        assert persisted is not None
        assert persisted.is_active is True
        assert persisted.consecutive_failures == 0


@pytest.mark.asyncio
async def test_schedule_crud_uses_new_pod_scoped_routes(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={"type": "SCHEDULED", "config": {"schedule_type": "ONCE"}},
        name_prefix="crud-workflow",
    )
    no_target = await authenticated_client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": ScheduleType.TIME.value,
            "config": {
                "scheduled_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
            },
        },
    )
    assert no_target.status_code == 422, no_target.text
    both_targets = await authenticated_client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "schedule_type": ScheduleType.TIME.value,
            "workflow_name": workflow["name"],
            "agent_name": "reviewer",
            "config": {
                "scheduled_at": (
                    datetime.now(timezone.utc) + timedelta(minutes=5)
                ).isoformat(),
            },
        },
    )
    assert both_targets.status_code == 422, both_targets.text
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        workflow_name=workflow["name"],
        config={
            "scheduled_at": (
                datetime.now(timezone.utc) + timedelta(minutes=5)
            ).isoformat(),
            "payload": {"source": "crud"},
        },
    )

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/schedules",
        params={"workflow_name": workflow["name"]},
    )
    assert listed.status_code == 200, listed.text
    assert any(item["id"] == schedule["id"] for item in listed.json()["items"])
    invalid_filter = await authenticated_client.get(
        f"/pods/{pod_id}/schedules",
        params={"workflow_name": workflow["name"], "agent_name": "reviewer"},
    )
    assert invalid_filter.status_code == 422, invalid_filter.text

    updated = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"is_active": False},
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["is_active"] is False

    deleted = await authenticated_client.delete(
        f"/pods/{pod_id}/schedules/{schedule['id']}"
    )
    assert deleted.status_code == 204, deleted.text
    result = await db_session.execute(
        select(Schedule).where(Schedule.id == schedule["id"])
    )
    assert result.scalar_one_or_none() is None


@pytest.mark.asyncio
async def test_schedule_name_is_pod_scoped_unique_and_filterable(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    scheduled_at = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        name="daily_triage",
        schedule_type=ScheduleType.TIME.value,
        agent_name=agent["name"],
        config={"scheduled_at": scheduled_at, "payload": {"source": "first"}},
    )
    assert schedule["name"] == "daily_triage"

    duplicate = await _create_schedule(
        authenticated_client,
        pod_id,
        name="daily_triage",
        schedule_type=ScheduleType.TIME.value,
        agent_name=agent["name"],
        config={
            "scheduled_at": (
                datetime.now(timezone.utc) + timedelta(minutes=6)
            ).isoformat(),
            "payload": {"source": "duplicate"},
        },
        expected_status=422,
    )
    assert "already exists" in duplicate["message"]

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/schedules",
        params={"name": "daily_triage"},
    )
    assert listed.status_code == 200, listed.text
    assert [item["id"] for item in listed.json()["items"]] == [schedule["id"]]

    renamed = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"name": "daily_triage_v2"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["name"] == "daily_triage_v2"


@pytest.mark.asyncio
async def test_time_schedule_frequency_policy_covers_create_update_and_reactivation(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_manager,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)

    rejected = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        agent_name=agent["name"],
        config={"cron": "*/5 * * * *"},
        expected_status=422,
    )
    assert rejected["code"] == "SCHEDULE_TOO_FREQUENT"
    assert rejected["message"] == (
        "Time schedules cannot run more frequently than every 15 minutes."
    )

    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        agent_name=agent["name"],
        config={"cron": "*/15 * * * *"},
    )
    rejected_update = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"config": {"cron": "* * * * *"}},
    )
    assert rejected_update.status_code == 422, rejected_update.text
    assert rejected_update.json()["code"] == "SCHEDULE_TOO_FREQUENT"

    async with db_manager.session_factory() as session, session.begin():
        persisted = await session.get(Schedule, UUID(schedule["id"]))
        assert persisted is not None
        persisted.config = {"cron": "*/5 * * * *"}
        persisted.is_active = False

    rejected_reactivation = await authenticated_client.patch(
        f"/pods/{pod_id}/schedules/{schedule['id']}",
        json={"is_active": True},
    )
    assert rejected_reactivation.status_code == 422, rejected_reactivation.text
    assert rejected_reactivation.json()["code"] == "SCHEDULE_TOO_FREQUENT"

    one_time = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.TIME.value,
        agent_name=agent["name"],
        config={
            "scheduled_at": (
                datetime.now(timezone.utc) + timedelta(minutes=2)
            ).isoformat()
        },
    )
    assert one_time["is_active"] is True


@pytest.mark.asyncio
async def test_datastore_schedule_fires_and_records_telemetry(
    authenticated_client: AsyncClient,
    fixed_test_org,
    worker,
):
    """A canonical INSERT schedule fires on a record insert and records telemetry."""
    _ = worker
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _create_datastore_table(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "schedule_records", "operations": ["INSERT"]},
        },
        name_prefix="datastore-telemetry-workflow",
    )
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config={"table_name": "schedule_records", "operations": ["INSERT"]},
    )

    record = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/tables/schedule_records/records",
        json={"data": {"source": "telemetry-record", "value": "hello"}},
    )
    assert record.status_code == 201, record.text

    run = await _wait_for_workflow_run(
        authenticated_client,
        pod_id,
        workflow["name"],
        source="telemetry-record",
    )
    assert run["start_type"] == "DATASTORE_EVENT"

    # Fire telemetry is recorded for debuggability.
    detail = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule['id']}"
    )
    assert detail.status_code == 200, detail.text
    body = detail.json()
    assert body["last_fire_status"] == "TRIGGERED"
    assert body["last_fired_at"] is not None


@pytest.mark.asyncio
async def test_datastore_schedule_requires_explicit_operations(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _create_datastore_table(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "schedule_records", "operations": ["INSERT"]},
        },
        name_prefix="datastore-noops-workflow",
    )
    response = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config={"table_name": "schedule_records"},
        expected_status=422,
    )
    assert "declare operations explicitly" in str(response)


@pytest.mark.asyncio
async def test_datastore_schedule_requires_table_editor_and_defaults_to_pod(
    authenticated_client: AsyncClient,
    async_client: AsyncClient,
    fixed_test_org,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _create_datastore_table(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "schedule_records", "operations": ["INSERT"]},
        },
        name_prefix="table-editor-schedule-workflow",
    )

    async def add_member(slug: str, role: str) -> dict[str, str]:
        user = await signup_user(async_client, slug)
        organization_member = await invite_org_member(
            authenticated_client,
            async_client,
            org_id=fixed_test_org["id"],
            user=user,
        )
        await add_pod_member(
            authenticated_client,
            pod_id=pod_id,
            organization_member_id=organization_member["id"],
            role=role,
            roles=[role],
        )
        return auth_headers(user)

    pod_user_headers = await add_member("datastore-schedule-user", "POD_USER")
    editor_headers = await add_member("datastore-schedule-editor", "POD_EDITOR")
    config = {"table_name": "schedule_records", "operations": ["INSERT"]}

    await _create_schedule(
        async_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config=config,
        headers=pod_user_headers,
        expected_status=403,
    )
    schedule = await _create_schedule(
        async_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config=config,
        headers=editor_headers,
    )
    assert schedule["visibility"] == "POD"

    missing_table = await _create_schedule(
        async_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config={"operations": ["INSERT"]},
        headers=editor_headers,
        expected_status=422,
    )
    assert "table_name" in str(missing_table)


@pytest.mark.asyncio
async def test_datastore_schedule_rejects_alias_operations(
    authenticated_client: AsyncClient,
    fixed_test_org,
):
    """Aliases (create/write) are no longer accepted — only INSERT/UPDATE/DELETE."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    await _create_datastore_table(authenticated_client, pod_id)
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "schedule_records", "operations": ["INSERT"]},
        },
        name_prefix="datastore-rejects-alias-workflow",
    )
    for alias in (["create"], ["write"]):
        await _create_schedule(
            authenticated_client,
            pod_id,
            schedule_type=ScheduleType.DATASTORE.value,
            workflow_name=workflow["name"],
            config={"table_name": "schedule_records", "operations": alias},
            expected_status=422,
        )


@pytest.mark.asyncio
async def test_matcher_skips_invalid_or_empty_operations(
    authenticated_client: AsyncClient,
    fixed_test_org,
    db_session: AsyncSession,
):
    """A row with unparseable or missing operations matches nothing (skipped)."""
    from sqlalchemy import text as sa_text

    from app.modules.schedule.repositories.schedule_repository import (
        ScheduleRepository as ScheduleRepositoryImpl,
    )
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    # Creating a DATASTORE schedule requires table-update permission on the
    # watched table, so the table has to exist first.
    await _create_datastore_table(authenticated_client, pod_id, "corrupt_records")
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={
            "type": "DATASTORE_EVENT",
            "config": {"table_name": "corrupt_records", "operations": ["INSERT"]},
        },
        name_prefix="corrupt-ops-workflow",
    )
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type=ScheduleType.DATASTORE.value,
        workflow_name=workflow["name"],
        config={"table_name": "corrupt_records", "operations": ["INSERT"]},
    )
    repo = ScheduleRepositoryImpl(uow=SqlAlchemyUnitOfWork(db_session))

    # Sanity: a canonical row matches.
    matched = await repo.find_by_pod_table_event(
        pod_id=UUID(pod_id), table_name="corrupt_records", operation="INSERT"
    )
    assert [str(item.id) for item in matched] == [schedule["id"]]

    # An unparseable operations value (e.g. a pre-fix alias row) is skipped.
    await db_session.execute(
        sa_text(
            "UPDATE schedules SET config = "
            '\'{"table_name": "corrupt_records", "operations": ["create"]}\' '
            "WHERE id = :id"
        ),
        {"id": schedule["id"]},
    )
    await db_session.commit()
    matched = await repo.find_by_pod_table_event(
        pod_id=UUID(pod_id), table_name="corrupt_records", operation="INSERT"
    )
    assert matched == []

    # A no-operations row also matches nothing.
    await db_session.execute(
        sa_text(
            'UPDATE schedules SET config = \'{"table_name": "corrupt_records"}\' '
            "WHERE id = :id"
        ),
        {"id": schedule["id"]},
    )
    await db_session.commit()
    matched = await repo.find_by_pod_table_event(
        pod_id=UUID(pod_id), table_name="corrupt_records", operation="INSERT"
    )
    assert matched == []
