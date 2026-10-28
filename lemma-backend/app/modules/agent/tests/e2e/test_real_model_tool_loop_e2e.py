"""A real model calls tools one after another within a single turn.

Every request after a turn's first tool call carries that call back, so this is
where an endpoint that wants something returned on it -- Gemini 3's thought
signature, kept in a call's ``extra_content`` -- needs it kept. The existing
real-model tests each make one call per turn and so never reach that second
request.

Runs against whatever ``system:lemma`` is configured with (``E2E_REAL=1`` and
the ``LEMMA_OPENAI_*`` variables); skipped in the mock lanes.
"""

from __future__ import annotations

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
    pytest.mark.provider,
    pytest.mark.real_llm,
    pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON),
]


async def test_a_real_model_makes_sequential_tool_calls_in_one_turn(
    authenticated_client, fixed_test_org, worker
):
    _ = worker
    pod_id = await _create_test_pod(authenticated_client, fixed_test_org)
    created = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Tool Loop Agent",
            "instruction": (
                "When asked to plan, call write_todos once with exactly two "
                "items, both pending. After it returns, call write_todos a "
                "second time marking the first item completed. Only after the "
                "second call returns, reply with the word: planned."
            ),
            "toolsets": ["TODO"],
            "agent_runtime": DEFAULT_AGENT_RUNTIME,
        },
    )
    assert created.status_code == 201, created.text
    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "tool_loop_agent", "title": "Tool loop", "type": "CHAT"},
    )
    assert conversation.status_code == 201, conversation.text
    conversation_id = conversation.json()["id"]

    events = await _post_sse(
        authenticated_client,
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        {"content": "Plan: draft the summary, then send it."},
    )
    _assert_completed_without_error(events)

    messages = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert messages.status_code == 200, messages.text
    items = messages.json()["items"]
    calls = [
        item
        for item in items
        if item["kind"] == "TOOL_CALL" and item["tool_name"] == "write_todos"
    ]
    returns = {item["tool_call_id"] for item in items if item["kind"] == "TOOL_RETURN"}
    assert len(calls) >= 2, items
    assert all(call["tool_call_id"] in returns for call in calls), items
    assert len({call["agent_run_id"] for call in calls}) == 1, items
    assert any(
        item["kind"] == "TEXT" and "planned" in (item["text"] or "").lower()
        for item in items
    ), items
