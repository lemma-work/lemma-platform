"""Agents-as-tools, the sub-agents toolset, and ``SubAgentService`` directly.

Most of this file exercises the full stack with a real model + the background
worker on the system Lemma runtime (system:lemma, backed by
LEMMA_OPENAI_API_KEY):
- A grant-based ``agent_<name>`` one-shot tool returns a STRING for a plain
  (no-output-schema) child and a structured DICT for a child with an
  output_schema, and links the child to the parent (``parent_id``).
- A named agent with the SUBAGENTS toolset self-spawns (no ``agent_name``); the
  child runs and answers and — being a sub-agent conversation — has no spawn
  tools (depth = 1).
- ``GET /conversations?parent_id=`` returns the spawned children.

Those are gated behind LEMMA_RUN_PROVIDER_E2E=1 (real provider creds + slow) on
each test individually, rather than for the whole module: real-provider tests
routinely skip in CI (and in `make coverage-backend-e2e-shard`, which
deselects `provider` entirely), and error/cancellation coverage of
``SubAgentService`` does not need a real model to reach it -- the ownership
guard and the stop-request write are exercised below with a real Postgres-backed
conversation graph and no LLM at all, so that coverage does not depend on
credentials being configured anywhere the suite runs.
"""

from __future__ import annotations

import os
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.tests.e2e.system_lemma_helpers import (
    SYSTEM_LEMMA_SKIP_REASON,
    system_lemma_available,
)
from app.modules.agent.tests.e2e.test_agent_e2e import (
    DEFAULT_AGENT_RUNTIME,
    _assert_completed_without_error,
    _create_test_pod,
    _post_sse,
)
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.subagents.models import (
    InteractSubagentRequest,
    QuerySubagentsRequest,
)
from app.modules.agent.tools.subagents.pydantic_adapter import (
    interact_subagent,
    query_subagents,
)
from app.modules.test_support.e2e.waiters import wait_for_status

pytestmark = pytest.mark.e2e


async def _create_agent(
    client, pod_id, name, instruction, *, output_schema=None, toolsets=None
):
    body = {
        "name": name,
        "instruction": instruction,
        "agent_runtime": DEFAULT_AGENT_RUNTIME,
    }
    if output_schema is not None:
        body["output_schema"] = output_schema
    if toolsets is not None:
        body["toolsets"] = toolsets
    response = await client.post(f"/pods/{pod_id}/agents", json=body)
    assert response.status_code == 201, response.text
    return response.json()


async def _grant_agent_execute(client, pod_id, parent_name, child_name):
    # Execute-only: agent.execute implies agent.read, so this single grant is
    # enough to dispatch the child. Serves as the execute-only regression for
    # the implication map — no separate agent.read needed.
    response = await client.put(
        f"/pods/{pod_id}/agents/{parent_name}/permissions",
        json={
            "grants": [
                {
                    "resource_type": "agent",
                    "resource_name": child_name,
                    "permission_ids": ["agent.execute"],
                }
            ]
        },
    )
    assert response.status_code == 200, response.text


async def _create_conversation(client, pod_id, agent_name):
    response = await client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": agent_name, "title": "subagent e2e", "type": "CHAT"},
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _list_children(client, pod_id, parent_conversation_id):
    response = await client.get(
        f"/pods/{pod_id}/conversations",
        params={"parent_id": parent_conversation_id},
    )
    assert response.status_code == 200, response.text
    return response.json()["items"]


async def _assistant_text(client, pod_id, conversation_id) -> str:
    response = await client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert response.status_code == 200, response.text
    return " ".join(
        (item.get("text") or "")
        for item in response.json()["items"]
        if item["role"] == "assistant" and item["kind"] == "TEXT"
    )


async def _wait_for_terminal_child(client, pod_id, parent_conversation_id, *, timeout=60.0):
    async def probe() -> dict:
        children = await _list_children(client, pod_id, parent_conversation_id)
        if not children:
            return {}
        convo = await client.get(f"/pods/{pod_id}/conversations/{children[0]['id']}")
        assert convo.status_code == 200, convo.text
        payload = convo.json()
        return {**payload, "status": str(payload.get("status") or "").upper()}

    # failed=set(): every caller fetches the terminal payload and asserts its own
    # expected status afterward (usually COMPLETED) -- FAILED and STOPPED are
    # both legitimate termini this helper must hand back rather than fail-fast
    # on, same as it did before (it only ever raised on the *timeout*, never on
    # reaching a particular status).
    return await wait_for_status(
        label=f"a terminal child of conversation {parent_conversation_id}",
        probe=probe,
        expected={"COMPLETED", "FAILED", "STOPPED"},
        failed=set(),
        timeout_seconds=timeout,
        interval_seconds=0.15,
    )


@pytest.mark.asyncio
@pytest.mark.provider
@pytest.mark.skipif(
    os.getenv("LEMMA_RUN_PROVIDER_E2E") != "1",
    reason="Set LEMMA_RUN_PROVIDER_E2E=1 to run real provider-backed e2e tests.",
)
@pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
async def test_grant_agent_tool_string_output_links_child(
    authenticated_client, fixed_test_org, worker
):
    _ = worker
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    await _create_agent(
        authenticated_client,
        pod_id,
        "echoer",
        "You echo text. Reply with EXACTLY the text you are given and nothing else.",
    )
    await _create_agent(
        authenticated_client,
        pod_id,
        "delegator",
        "You have a tool named agent_echoer. When the user gives you a phrase to "
        "echo, call agent_echoer exactly once with input set to that phrase, then "
        "reply with exactly what the tool returned.",
    )
    await _grant_agent_execute(authenticated_client, pod_id, "delegator", "echoer")

    conversation_id = await _create_conversation(authenticated_client, pod_id, "delegator")
    events = await _post_sse(
        authenticated_client,
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        {"content": "Echo this phrase exactly: ZEBRA_TOKEN_77"},
    )
    _assert_completed_without_error(events)

    # The parent got the child's answer back as a STRING (no output_schema) and
    # echoed it verbatim as its own final answer.
    parent_text = await _assistant_text(authenticated_client, pod_id, conversation_id)
    assert "ZEBRA_TOKEN_77" in parent_text

    child = await _wait_for_terminal_child(
        authenticated_client, pod_id, conversation_id, timeout=60.0
    )
    assert child["status"].upper() == "COMPLETED", child
    # An unstructured child finalizes its answer as {"answer": <text>}.
    output = child.get("output")
    assert isinstance(output, dict) and "ZEBRA_TOKEN_77" in str(output.get("answer")), output
    child_text = await _assistant_text(authenticated_client, pod_id, child["id"])
    assert "ZEBRA_TOKEN_77" in child_text


@pytest.mark.asyncio
@pytest.mark.provider
@pytest.mark.skipif(
    os.getenv("LEMMA_RUN_PROVIDER_E2E") != "1",
    reason="Set LEMMA_RUN_PROVIDER_E2E=1 to run real provider-backed e2e tests.",
)
@pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
async def test_grant_agent_tool_structured_output_returns_dict(
    authenticated_client, fixed_test_org, worker
):
    _ = worker
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    await _create_agent(
        authenticated_client,
        pod_id,
        "classifier",
        "Classify the sentiment of the input as 'positive' or 'negative' and set "
        "the label field accordingly.",
        output_schema={
            "type": "object",
            "properties": {"label": {"type": "string"}},
            "required": ["label"],
        },
    )
    await _create_agent(
        authenticated_client,
        pod_id,
        "router",
        "You have a tool named agent_classifier. Call it once with input set to the "
        "user's text, then report the label it returned.",
    )
    await _grant_agent_execute(authenticated_client, pod_id, "router", "classifier")

    conversation_id = await _create_conversation(authenticated_client, pod_id, "router")
    events = await _post_sse(
        authenticated_client,
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        {"content": "Classify this: I absolutely love this product!"},
    )
    _assert_completed_without_error(events)

    # The parent got a structured DICT back from the tool and reported the label.
    parent_text = await _assistant_text(authenticated_client, pod_id, conversation_id)
    assert "positive" in parent_text.lower()

    child = await _wait_for_terminal_child(
        authenticated_client, pod_id, conversation_id, timeout=60.0
    )
    assert child["status"].upper() == "COMPLETED", child
    # A child WITH an output_schema finalizes a structured dict answer.
    output = child.get("output")
    assert isinstance(output, dict) and output.get("label") == "positive", output


@pytest.mark.asyncio
@pytest.mark.provider
@pytest.mark.skipif(
    os.getenv("LEMMA_RUN_PROVIDER_E2E") != "1",
    reason="Set LEMMA_RUN_PROVIDER_E2E=1 to run real provider-backed e2e tests.",
)
@pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON)
async def test_named_agent_self_spawns_via_subagents_toolset(
    authenticated_client, fixed_test_org, worker
):
    _ = worker
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    # SUBAGENTS toolset → spawn_subagent. Self-spawn (no agent_name) launches
    # another instance of looper; the child (a sub-agent conversation) has no
    # spawn tools, so it just answers.
    await _create_agent(
        authenticated_client,
        pod_id,
        "looper",
        "When the user asks, call spawn_subagent with NO agent_name and input set "
        "to 'Reply with exactly the word DELTA99'. Then call interact_subagent with "
        "action='await' and the returned conversation_id and run_id and report the "
        "answer. If you have no spawn_subagent tool, just reply with exactly DELTA99.",
        toolsets=["SUBAGENTS"],
    )

    conversation_id = await _create_conversation(authenticated_client, pod_id, "looper")
    events = await _post_sse(
        authenticated_client,
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        {"content": "Please delegate the DELTA99 subtask to a sub-agent of yourself."},
    )
    _assert_completed_without_error(events)
    # The parent drove spawn_subagent (self, no agent_name) + interact_subagent(await).
    parent_text = await _assistant_text(authenticated_client, pod_id, conversation_id)
    assert "DELTA99" in parent_text

    child = await _wait_for_terminal_child(authenticated_client, pod_id, conversation_id)
    assert child["status"].upper() == "COMPLETED", child
    # The child is a sub-agent conversation (depth=1: no spawn tools), so it just
    # answered DELTA99 directly.
    child_text = await _assistant_text(authenticated_client, pod_id, child["id"])
    assert "DELTA99" in child_text


# --------------------------------------------------------------------------
# SubAgentService error/cancellation paths, driven directly (no LLM, no
# worker). `_owned_child`'s ownership guard and `stop()`'s write to a real
# active run are both deterministic once a conversation graph exists, so
# there is nothing here a real model turn would add.


async def _create_running_child(
    authenticated_client,
    pod_id: str,
    *,
    agent_name: str,
    parent_conversation_id: str,
) -> UUID:
    """A real child conversation, linked and RUNNING -- no background job.

    Mirrors what `SubAgentService.spawn` leaves behind (`parent_id` set, a
    RUNNING agent run) the instant a child starts and before any worker picks
    it up. Built directly rather than through `spawn()` so the tests below
    have something deterministic to stop, with no race against a worker
    actually finishing it first.
    """
    response = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "title": "spawned child",
            "type": "CHAT",
            "parent_id": parent_conversation_id,
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    payload = response.json()
    conversation_id = UUID(payload["id"])
    agent_id = UUID(payload["agent_id"]) if payload.get("agent_id") else None

    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ConversationRepository(uow).create_agent_run(
            conversation_id=conversation_id,
            agent_id=agent_id,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            metadata={"source": "subagent_direct_e2e"},
        )
        await uow.commit()
    return conversation_id


@pytest.mark.asyncio
async def test_query_and_interact_subagent_reject_conversations_this_agent_did_not_spawn(
    authenticated_client, fixed_test_org, fixed_test_user
):
    """The ownership guard shared by every subagent operation (`_owned_child`).

    Neither a conversation that does not exist nor one that exists but was
    spawned under a *different* parent may be read or driven -- both come back
    as a tool-shaped failure (`{"success": False, "error": ...}`), never an
    unhandled exception reaching the harness.
    """
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    await _create_agent(authenticated_client, pod_id, "owner", "Owns children.")
    await _create_agent(authenticated_client, pod_id, "stranger", "Owns other children.")

    owner_conversation_id = await _create_conversation(authenticated_client, pod_id, "owner")
    stranger_conversation_id = await _create_conversation(
        authenticated_client, pod_id, "stranger"
    )
    # A real, running child -- but spawned under "stranger", not "owner".
    unrelated_child_id = await _create_running_child(
        authenticated_client,
        pod_id,
        agent_name="stranger",
        parent_conversation_id=stranger_conversation_id,
    )

    ctx = SimpleNamespace(
        deps=BaseAgentContext(
            user_id=UUID(fixed_test_user["id"]),
            pod_id=UUID(pod_id),
            conversation_id=UUID(owner_conversation_id),
            agent_name="owner",
        )
    )

    missing = await query_subagents(
        ctx, QuerySubagentsRequest(mode="messages", conversation_id=str(uuid4()))
    )
    assert missing["success"] is False
    assert "not spawned by this agent" in missing["error"]

    unrelated = await interact_subagent(
        ctx,
        InteractSubagentRequest(action="stop", conversation_id=str(unrelated_child_id)),
    )
    assert unrelated["success"] is False
    assert "not spawned by this agent" in unrelated["error"]

    # Rejection must have no side effects: the stranger's own child run is
    # exactly as it was.
    still_running = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{unrelated_child_id}"
    )
    assert still_running.status_code == status.HTTP_200_OK, still_running.text
    assert still_running.json()["status"] == "RUNNING"


@pytest.mark.asyncio
async def test_interact_subagent_stop_requests_a_real_running_child(
    authenticated_client, fixed_test_org, fixed_test_user
):
    """Cancellation: `stop()` on a real, owned child moves it to STOP_REQUESTED,
    and is idempotent against a run that is already stopping."""
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    await _create_agent(
        authenticated_client, pod_id, "supervisor", "Spawns and stops children."
    )
    await _create_agent(authenticated_client, pod_id, "worker_agent", "Does the work.")

    parent_conversation_id = await _create_conversation(
        authenticated_client, pod_id, "supervisor"
    )
    child_id = await _create_running_child(
        authenticated_client,
        pod_id,
        agent_name="worker_agent",
        parent_conversation_id=parent_conversation_id,
    )

    before = await authenticated_client.get(f"/pods/{pod_id}/conversations/{child_id}")
    assert before.status_code == status.HTTP_200_OK, before.text
    assert before.json()["status"] == "RUNNING"

    ctx = SimpleNamespace(
        deps=BaseAgentContext(
            user_id=UUID(fixed_test_user["id"]),
            pod_id=UUID(pod_id),
            conversation_id=UUID(parent_conversation_id),
            agent_name="supervisor",
        )
    )

    stopped = await interact_subagent(
        ctx, InteractSubagentRequest(action="stop", conversation_id=str(child_id))
    )
    assert stopped["success"] is True
    assert stopped["conversation_id"] == str(child_id)
    assert stopped["status"] == "STOP_REQUESTED"

    after = await authenticated_client.get(f"/pods/{pod_id}/conversations/{child_id}")
    assert after.status_code == status.HTTP_200_OK, after.text
    assert after.json()["status"] == "STOP_REQUESTED"

    # STOP_REQUESTED still counts as active (nothing has finished the run
    # yet -- there is no worker here to do that), so a second stop call finds
    # it again and is a harmless no-op rather than an error.
    stopped_again = await interact_subagent(
        ctx, InteractSubagentRequest(action="stop", conversation_id=str(child_id))
    )
    assert stopped_again["success"] is True
    assert stopped_again["status"] == "STOP_REQUESTED"
