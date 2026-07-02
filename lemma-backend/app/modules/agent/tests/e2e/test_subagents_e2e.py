"""Real-LLM e2e for agents-as-tools and the sub-agents toolset.

Exercises the full stack with a real model + the background worker on the
system Lemma runtime (system:lemma, backed by LEMMA_OPENAI_API_KEY):
- A grant-based ``agent_<name>`` one-shot tool returns a STRING for a plain
  (no-output-schema) child and a structured DICT for a child with an
  output_schema, and links the child to the parent (``parent_id``).
- A named agent with the SUBAGENTS toolset self-spawns (no ``agent_name``); the
  child runs and answers and — being a sub-agent conversation — has no spawn
  tools (depth = 1).
- ``GET /conversations?parent_id=`` returns the spawned children.

Gated behind LEMMA_RUN_PROVIDER_E2E=1 (real provider creds + slow).
"""

from __future__ import annotations

import asyncio
import os

import pytest

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

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.getenv("LEMMA_RUN_PROVIDER_E2E") != "1",
        reason="Set LEMMA_RUN_PROVIDER_E2E=1 to run real provider-backed e2e tests.",
    ),
    pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON),
]


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
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        children = await _list_children(client, pod_id, parent_conversation_id)
        if children:
            convo = await client.get(
                f"/pods/{pod_id}/conversations/{children[0]['id']}"
            )
            assert convo.status_code == 200, convo.text
            payload = convo.json()
            if str(payload.get("status") or "").upper() in {
                "COMPLETED",
                "FAILED",
                "STOPPED",
            }:
                return payload
        if asyncio.get_event_loop().time() >= deadline:
            raise AssertionError(
                f"No terminal child for parent {parent_conversation_id}; children={children}"
            )
        await asyncio.sleep(1.0)


@pytest.mark.asyncio
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
