"""E2E: the pod's agent sets what the pod owns, through the same checks a person has.

``set_what_you_own`` is the setup chat's way to keep the measure once the
tables it counts exist. The pod's own assistant holds the invoking person's
permissions, so it can; a named agent holds only its grants, so it is sent to
ask for approval like any other write it was not granted. Either way the
measure is read once before it is kept, and the brief then tells the agent
what it owns.
"""

from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.authorization.delegation import is_pod_default_agent
from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.pod.pod_outcome_tool import set_what_you_own
from app.modules.pod.contracts.members import pod_profile
from app.modules.pod.contracts.outcome import OutcomeInput

pytestmark = pytest.mark.e2e


async def _pod_with_posts(client, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={"name": f"content-{uuid4().hex[:8]}", "organization_id": org_id},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    pod_id = response.json()["id"]
    response = await client.post(
        f"/pods/{pod_id}/datastore/tables",
        json={
            "name": "posts",
            "primary_key_column": "id",
            "enable_rls": False,
            "columns": [
                {"name": "id", "type": "UUID", "required": True, "auto": True},
                {"name": "title", "type": "TEXT", "required": True},
            ],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    for title in ("one", "two"):
        response = await client.post(
            f"/pods/{pod_id}/datastore/tables/posts/records",
            json={"data": {"title": title}},
        )
        assert response.status_code == status.HTTP_201_CREATED, response.text
    return pod_id


def _run_ctx(*, user_id: str, pod_id: str, workload_id: str, agent_name: str):
    workload = UUID(workload_id)
    return SimpleNamespace(
        deps=BaseAgentContext(
            user_id=UUID(user_id),
            pod_id=UUID(pod_id),
            conversation_id=uuid4(),
            workload_type="agent",
            workload_id=workload,
            agent_name=agent_name,
            is_pod_default_agent=is_pod_default_agent(workload, pod_id=UUID(pod_id)),
        )
    )


OWNS = OutcomeInput.model_validate(
    {
        "sentence": "Two posts go out every week.",
        "kind": "week",
        "target": 2,
        "unit": "posts",
        "measure": "SELECT count(*) FROM posts",
    }
)


@pytest.mark.asyncio
async def test_the_pods_own_agent_sets_what_it_owns_and_is_told_it(
    authenticated_client, fixed_test_org, fixed_test_user
):
    pod_id = await _pod_with_posts(authenticated_client, fixed_test_org["id"])
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=pod_id,
        agent_name="pod_default",
    )
    result = await set_what_you_own(ctx, OWNS)  # type: ignore[arg-type]
    assert result["success"] is True, result
    assert result["value"] == 2

    pod = (await authenticated_client.get(f"/pods/{pod_id}")).json()
    assert pod["config"]["outcome"]["measure"] == "SELECT count(*) FROM posts"
    async with async_session_maker() as session:
        profile = await pod_profile(session, UUID(pod_id))
    assert profile.owns is not None
    assert profile.owns.startswith("Two posts go out every week.")
    assert "/plan.md" in profile.owns


@pytest.mark.asyncio
async def test_a_measure_that_is_not_one_number_comes_back_to_the_agent(
    authenticated_client, fixed_test_org, fixed_test_user
):
    pod_id = await _pod_with_posts(authenticated_client, fixed_test_org["id"])
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=pod_id,
        agent_name="pod_default",
    )
    rows = OWNS.model_copy(update={"measure": "SELECT title FROM posts"})
    result = await set_what_you_own(ctx, rows)  # type: ignore[arg-type]
    assert result["success"] is False
    assert result["code"] == "DATASTORE_MEASURE_INVALID"
    pod = (await authenticated_client.get(f"/pods/{pod_id}")).json()
    assert "outcome" not in pod["config"]


@pytest.mark.asyncio
async def test_a_named_agent_without_the_grant_is_sent_to_ask(
    authenticated_client, fixed_test_org, fixed_test_user
):
    pod_id = await _pod_with_posts(authenticated_client, fixed_test_org["id"])
    response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={"name": f"writer-{uuid4().hex[:6]}", "instruction": "Answer briefly."},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    agent = response.json()
    ctx = _run_ctx(
        user_id=fixed_test_user["id"],
        pod_id=pod_id,
        workload_id=agent["id"],
        agent_name=agent["name"],
    )
    result = await set_what_you_own(ctx, OWNS)  # type: ignore[arg-type]
    assert result["success"] is False
    assert result.get("needs_approval") is True, result
    pod = (await authenticated_client.get(f"/pods/{pod_id}")).json()
    assert "outcome" not in pod["config"]
