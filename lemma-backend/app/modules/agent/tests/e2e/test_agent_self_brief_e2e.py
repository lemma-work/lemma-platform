"""What an agent is told about itself, against a real database.

Every unit test around ``## You`` and the inventory runs against a stubbed
repository, so all of them would keep passing if a column name were wrong, a
relationship were loaded lazily in async code, or a status value did not match
what the table actually stores. The sections here are assembled from five
hand-written reads across four modules; this is the test that runs them.

Three claims:

  * the default agent answers to the pod's name, and its start date is the
    pod's;
  * a schedule configured to start it reads back as its own, and a table
    created shared says so on the line the agent reads;
  * the whole brief survives a pod with nothing in it, which is the state every
    pod is in on its first day.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.modules.agent.infrastructure.repositories import AgentRepository
from app.modules.agent.services.agent_context_brief import AgentContextBriefBuilder
from app.modules.agent.services import agent_context_brief as brief_mod
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)

pytestmark = pytest.mark.e2e


@pytest.fixture(autouse=True)
def _no_brief_cache(monkeypatch):
    """Read through on every build.

    The brief is cached per (agent, pod, user) for a minute in production. These
    tests create a pod, add something to it and read the brief back inside that
    window, so a live cache would serve the empty pod every time and the test
    would pass without ever running the query it exists to run.
    """
    monkeypatch.setattr(brief_mod, "_get_brief_cache", lambda: None)


async def _create_pod(authenticated_client, fixed_test_org, name: str) -> dict:
    response = await authenticated_client.post(
        "/pods",
        json={
            "name": name,
            "type": "HYBRID",
            "organization_id": fixed_test_org["id"],
            "description": "Where the support work lives.",
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return response.json()


async def _default_agent_brief(*, pod_id: str, user_id: str) -> str:
    """The brief a fresh conversation with the pod's own agent would carry."""
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    async with uow_factory() as uow:
        agent = await AgentRepository(uow).get_by_pod_and_name(
            pod_id=UUID(pod_id), name=DEFAULT_POD_AGENT_NAME
        )
    assert agent is not None, "every pod has its own agent as a row"
    conversation = type(
        "_Conversation", (), {"id": uuid4(), "is_pod_assistant": True, "metadata": {}}
    )()
    return await AgentContextBriefBuilder(uow_factory).build(
        agent=agent,
        conversation=conversation,
        user_id=UUID(user_id),
        pod_id=UUID(pod_id),
    )


@pytest.mark.asyncio
async def test_the_default_agent_is_told_the_pods_name_and_purpose(
    authenticated_client, fixed_test_org, fixed_test_user
):
    """The default agent answers to the pod's name.

    Rendering the platform's default responder name here would give every pod
    in an organization the same one.
    """
    name = f"support-{uuid4().hex[:8]}"
    pod = await _create_pod(authenticated_client, fixed_test_org, name)

    brief = await _default_agent_brief(pod_id=pod["id"], user_id=fixed_test_user["id"])

    assert "## You" in brief
    assert f"**{name}**" in brief
    assert "What this pod is for: Where the support work lives." in brief
    # Tenure comes off the pod's own created_at, so a brand new pod still has a
    # date rather than a hole.
    assert "Here since " in brief


@pytest.mark.asyncio
async def test_a_schedule_configured_to_start_the_agent_is_named_as_its_own(
    authenticated_client, fixed_test_org, fixed_test_user
):
    """The reads behind this are hand-written; only a real pod runs them."""
    pod = await _create_pod(
        authenticated_client, fixed_test_org, f"sched-{uuid4().hex[:8]}"
    )
    response = await authenticated_client.post(
        f"/pods/{pod['id']}/schedules",
        json={
            "name": "morning-sweep",
            "schedule_type": "TIME",
            "agent_name": "POD_DEFAULT",
            "instruction": "Check for stale rows and report what you find.",
            "config": {"cron": "0 9 * * 1-5"},
        },
    )
    assert response.status_code in (
        status.HTTP_200_OK,
        status.HTTP_201_CREATED,
    ), response.text

    brief = await _default_agent_brief(pod_id=pod["id"], user_id=fixed_test_user["id"])

    assert "Schedules configured to start you:" in brief
    assert "morning-sweep" in brief
    assert "cron `0 9 * * 1-5`" in brief
    assert "Check for stale rows" in brief


@pytest.mark.asyncio
async def test_a_table_line_says_who_can_see_its_rows(
    authenticated_client, fixed_test_org, fixed_test_user
):
    """`enable_rls` is the fact the brief most obviously used to drop.

    An agent told to land durable state in a table, and not told this, builds
    the team's ledger as one person's private notebook.
    """
    pod = await _create_pod(
        authenticated_client, fixed_test_org, f"tables-{uuid4().hex[:8]}"
    )
    for table_name, enable_rls in (("team_ledger", False), ("my_notes", True)):
        response = await authenticated_client.post(
            f"/pods/{pod['id']}/datastore/tables",
            json={
                "table_name": table_name,
                "primary_key_column": "id",
                "enable_rls": enable_rls,
                "columns": [
                    {
                        "name": "title",
                        "type": "TEXT",
                        "required": True,
                        "description": "what it is",
                    }
                ],
            },
        )
        assert response.status_code in (
            status.HTTP_200_OK,
            status.HTTP_201_CREATED,
        ), response.text

    brief = await _default_agent_brief(pod_id=pod["id"], user_id=fixed_test_user["id"])

    ledger = next(line for line in brief.splitlines() if "team_ledger" in line)
    notes = next(line for line in brief.splitlines() if "my_notes" in line)
    assert "RLS off" in ledger
    assert "RLS on" in notes
    # The column facts a write depends on, none of which used to render.
    assert "title:TEXT(required)" in ledger
    assert '"what it is"' in ledger
    # The system columns say `auto`, not `required`. That distinction is the
    # point: an agent that reads "required" on `created_at` supplies it and the
    # write comes back rejected.
    assert "id:UUID(auto" in ledger
    assert "created_at:DATETIME(auto)" in ledger
    assert "required" not in ledger.split("created_at")[1]


@pytest.mark.asyncio
async def test_an_empty_pod_still_renders_a_whole_brief(
    authenticated_client, fixed_test_org, fixed_test_user
):
    """The state every pod is in on its first day.

    Five of the sections here are best-effort reads. A pod with nothing in it
    must produce a brief with no empty headings and no exception, because that
    is the brief a person's very first conversation carries.
    """
    pod = await _create_pod(
        authenticated_client, fixed_test_org, f"empty-{uuid4().hex[:8]}"
    )

    brief = await _default_agent_brief(pod_id=pod["id"], user_id=fixed_test_user["id"])

    assert brief.startswith("# Runtime Context")
    assert "## You" in brief
    # The person who made it is in the pod, so this one is never empty -- and it
    # carries the id `message_user` actually takes.
    assert "## People here" in brief
    assert "(to: " in brief
    for empty_section in ("## Tables", "## Workflows", "## Agents", "## Functions"):
        assert empty_section not in brief, (
            f"{empty_section} rendered as a heading over nothing"
        )


@pytest.mark.asyncio
async def test_a_members_brief_excludes_another_members_private_schedule(
    authenticated_client, async_client, fixed_test_org
):
    """Being in a pod is not permission to read everything in it.

    The first version of the schedule and workflow summaries selected on
    ``pod_id`` alone. A schedule carries its own visibility and owner, and an
    agent-targeting schedule created by a pod user defaults to **PERSONAL** --
    so one member's private standing work, instruction text included, went into
    another member's agent prompt.

    Two real members and a real private schedule, because this is exactly the
    check a stubbed repository cannot make.
    """
    created = await authenticated_client.post(
        "/pods",
        json={
            "name": f"visibility-{uuid4().hex[:8]}",
            "type": "HYBRID",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    pod_id = created.json()["id"]

    async def _member(slug: str) -> tuple[dict, dict[str, str]]:
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
            role="POD_USER",
            roles=["POD_USER"],
        )
        return user, auth_headers(user)

    owner, owner_headers = await _member(f"brief-owner-{uuid4().hex[:6]}")
    peer, _ = await _member(f"brief-peer-{uuid4().hex[:6]}")

    private = await async_client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "name": "owners-private-sweep",
            "schedule_type": "TIME",
            "agent_name": "POD_DEFAULT",
            "instruction": "SECRET-INSTRUCTION-DO-NOT-LEAK",
            "config": {"cron": "0 9 * * 1-5"},
        },
        headers=owner_headers,
    )
    assert private.status_code in (
        status.HTTP_200_OK,
        status.HTTP_201_CREATED,
    ), private.text
    assert private.json()["visibility"] == "PERSONAL", (
        "only meaningful while an agent schedule defaults to PERSONAL"
    )

    owner_brief = await _default_agent_brief(pod_id=pod_id, user_id=owner["id"])
    assert "owners-private-sweep" in owner_brief, "the owner should see their own"

    peer_brief = await _default_agent_brief(pod_id=pod_id, user_id=peer["id"])
    assert "owners-private-sweep" not in peer_brief
    assert "SECRET-INSTRUCTION-DO-NOT-LEAK" not in peer_brief
    # The count is filtered too: a total taken over everything would tell the
    # reader how many schedules they are not allowed to see.
    assert "more schedules not listed" not in peer_brief
