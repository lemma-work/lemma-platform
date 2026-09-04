"""The memory round trip, against a real database, datastore and authorization.

Everything else about memory is unit-tested against stand-ins. This is the one
test that proves the thing the feature actually promises: a fact written in one
conversation is in the agent's prompt for the next one. Every unit test in this
area passes against a fake file service, and would keep passing if the grant, the
folder or the authorization context were wrong.

Three claims, each a different way the feature has to hold together:

  * granting MEMORY provisions `/memory` and the permission to write it, because
    a capability without its grant is a switch that does nothing;
  * an agent with MEMORY reads back what was written, across conversations;
  * an agent without it sees no memory section at all.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.infrastructure.repositories import AgentRepository
from app.modules.agent.services.agent_context_brief import AgentContextBriefBuilder
from app.modules.agent.services import agent_memory_brief as memory_mod

pytestmark = pytest.mark.e2e


async def _create_pod(authenticated_client, fixed_test_org) -> str:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": f"memory-{uuid4().hex[:8]}",
            "type": "HYBRID",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()["id"]


async def _create_agent(
    authenticated_client, pod_id: str, name: str, toolsets: list[str]
) -> dict:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": name,
            "instruction": "Remember things.",
            "toolsets": toolsets,
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _grants(authenticated_client, pod_id: str, name: str) -> dict[str, list[str]]:
    response = await authenticated_client.get(
        f"/pods/{pod_id}/agents/{name}/permissions"
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return {
        grant["resource_name"]: grant["permission_ids"]
        for grant in response.json().get("grants") or []
    }


async def _write_memory(authenticated_client, pod_id: str, text: str) -> None:
    response = await authenticated_client.post(
        f"/pods/{pod_id}/datastore/files",
        data={"directory_path": "/memory", "search_enabled": "false"},
        files={"data": ("AGENTS.md", text.encode("utf-8"), "text/markdown")},
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text


async def _brief(*, agent_name: str, pod_id: str, user_id: str) -> str:
    """The runtime brief a fresh conversation with this agent would carry."""
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    async with uow_factory() as uow:
        agent = await AgentRepository(uow).get_by_pod_and_name(
            pod_id=UUID(pod_id), name=agent_name
        )
    assert agent is not None
    # A conversation that has never run before -- the case that matters, since
    # most runs are the first of their conversation.
    conversation = type(
        "_Conversation", (), {"id": uuid4(), "is_pod_assistant": False}
    )()
    return await AgentContextBriefBuilder(uow_factory).build(
        agent=agent,
        conversation=conversation,
        user_id=UUID(user_id),
        pod_id=UUID(pod_id),
        toolsets=[AgentToolset(name) for name in agent.toolsets],
    )


@pytest.fixture(autouse=True)
def _no_memory_cache(monkeypatch):
    """Read through to the datastore on every build.

    The cache is invalidated by writes in production, but this test writes
    through the HTTP API in the same process and then reads the brief directly;
    pinning the cache off keeps it a test of the round trip rather than of the
    invalidation, which has its own unit tests.
    """
    monkeypatch.setattr(memory_mod, "_get_cache", lambda: None)


@pytest.mark.asyncio
async def test_granting_memory_provisions_the_folder_and_the_permission(
    authenticated_client, fixed_test_org
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)

    await _create_agent(authenticated_client, pod_id, "rememberer", ["MEMORY", "POD"])

    grants = await _grants(authenticated_client, pod_id, "rememberer")
    assert "/memory" in grants, grants
    assert "folder.write" in grants["/memory"]

    listing = await authenticated_client.get(
        f"/pods/{pod_id}/datastore/files", params={"directory_path": "/"}
    )
    assert listing.status_code == status.HTTP_200_OK, listing.text
    assert "/memory" in {item["path"] for item in listing.json()["items"]}


@pytest.mark.asyncio
async def test_turning_memory_off_takes_the_grant_away(
    authenticated_client, fixed_test_org
):
    """The grant is derived, not stored, so it has to follow the toolset both
    ways -- otherwise disabling a capability leaves its access behind."""
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    await _create_agent(authenticated_client, pod_id, "forgetter", ["MEMORY", "POD"])

    response = await authenticated_client.patch(
        f"/pods/{pod_id}/agents/forgetter", json={"toolsets": ["POD"]}
    )
    assert response.status_code == status.HTTP_200_OK, response.text

    assert "/memory" not in await _grants(authenticated_client, pod_id, "forgetter")


@pytest.mark.asyncio
async def test_a_fact_written_once_is_in_the_next_conversations_prompt(
    authenticated_client, fixed_test_org, fixed_test_user
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    await _create_agent(authenticated_client, pod_id, "rememberer", ["MEMORY", "POD"])

    await _write_memory(
        authenticated_client, pod_id, "- billing: cycle starts on the 3rd"
    )

    brief = await _brief(
        agent_name="rememberer", pod_id=pod_id, user_id=fixed_test_user["id"]
    )

    assert "## Your Memory" in brief
    assert "### Pod (shared) — `/memory/AGENTS.md`" in brief
    assert "cycle starts on the 3rd" in brief
    # The agent is told its own folders, not left to work the slug out.
    assert "/memory/agents/rememberer/" in brief


@pytest.mark.asyncio
async def test_an_agent_without_the_capability_is_told_nothing(
    authenticated_client, fixed_test_org, fixed_test_user
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org)
    await _create_agent(authenticated_client, pod_id, "rememberer", ["MEMORY", "POD"])
    await _create_agent(authenticated_client, pod_id, "plain", ["POD"])

    await _write_memory(authenticated_client, pod_id, "- billing: the 3rd")

    brief = await _brief(
        agent_name="plain", pod_id=pod_id, user_id=fixed_test_user["id"]
    )

    assert "## Your Memory" not in brief
    assert "the 3rd" not in brief
