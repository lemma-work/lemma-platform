"""An agent's reasoning never arrives as its answer, however the model sends it.

The reported failure was a conversation whose fourth turn answered with the
model's own chain of thought. It took four turns because the cause was
cumulative -- replaying stored thoughts as ``<think>`` text taught the model
that reasoning belongs in the answer -- so a single-turn test could not have
found it, and none did.

These run the real harness, persistence, streaming and worker; only the model's
next response is scripted. Both shapes a model can use are covered, because they
fail differently: reasoning as its own part (what a healthy reasoning model
sends) and reasoning inlined in the answer as tags (what one sends after the
conversation has taught it to).
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi import status

from app.modules.test_support.e2e.scripted_model import (
    script_inline_reasoning,
    script_text,
    script_thinking,
)

from app.modules.agent.tests.e2e.test_agent_hermetic_journeys_e2e import (
    _create_pod,
    _create_runtime_profile,
    _send_message,
)

pytestmark = pytest.mark.e2e

OPEN_TAG = chr(60) + "think"


async def _agent_and_conversation(
    authenticated_client,
    fixed_test_org,
    e2e_settings,
    script,
):
    runtime = await _create_runtime_profile(
        authenticated_client, fixed_test_org, e2e_settings
    )
    pod = await _create_pod(authenticated_client, fixed_test_org)
    pod_id = pod["id"]
    agent_name = f"reasoning_{uuid4().hex[:8]}"

    created = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": agent_name,
            "instruction": "Reply using the scripted deterministic model.",
            "agent_runtime": {
                "profile_id": runtime["id"],
                "model_name": "mock-safe-model",
            },
            "toolsets": [],
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={
            "agent_name": agent_name,
            "metadata": {"mock_llm_script": script, "source": "reasoning-e2e"},
        },
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    return pod_id, conversation.json()["id"]


async def _messages(authenticated_client, pod_id, conversation_id):
    response = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    return response.json()["items"]


def _tokens(events, kind):
    return "".join(
        str(event.get("data", ""))
        for event in events
        if event["type"] == "token" and event.get("kind") == kind
    )


def _assert_no_answer_carries_reasoning(items):
    """No assistant TEXT row may contain a thinking tag. The whole promise."""
    for item in items:
        if item["role"] == "assistant" and item["kind"] == "TEXT":
            assert OPEN_TAG not in (item["text"] or "").lower(), item


@pytest.mark.asyncio
async def test_reasoning_sent_as_its_own_part_is_stored_and_streamed_as_thinking(
    authenticated_client, fixed_test_org, e2e_settings, worker
):
    del worker
    pod_id, conversation_id = await _agent_and_conversation(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
        [script_thinking("Weighing the options carefully.", "Paris.")],
    )

    events = await _send_message(
        authenticated_client, pod_id, conversation_id, "Capital of France?"
    )
    assert events[-1]["type"] == "completed", events

    # The two lanes stay apart on the wire, which is what lets a client show the
    # thought in a trace and the answer in a bubble.
    assert "Weighing the options" in _tokens(events, "thinking")
    assert "Paris." in _tokens(events, "text")
    assert OPEN_TAG not in _tokens(events, "text").lower()

    items = await _messages(authenticated_client, pod_id, conversation_id)
    thinking = [item for item in items if item["kind"] == "THINKING"]
    answers = [
        item
        for item in items
        if item["role"] == "assistant" and item["kind"] == "TEXT" and item["text"]
    ]
    assert [item["text"] for item in thinking] == ["Weighing the options carefully."]
    assert [item["text"] for item in answers] == ["Paris."]
    assert answers[0]["metadata"].get("is_final_answer") is True


@pytest.mark.asyncio
async def test_reasoning_inlined_in_the_answer_is_reclassified_not_delivered(
    authenticated_client, fixed_test_org, e2e_settings, worker
):
    """The shape that reached users.

    The mock chunks the answer at a fixed width, so the opening tag straddles a
    delta boundary exactly as Fireworks sends it -- the case pydantic-ai's own
    tag handling misses, because it only splits when one delta *is* the tag.
    """
    del worker
    pod_id, conversation_id = await _agent_and_conversation(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
        [
            script_inline_reasoning(
                "General knowledge question. Answer it directly and concisely.",
                "Paris.",
            )
        ],
    )

    events = await _send_message(
        authenticated_client, pod_id, conversation_id, "Capital of France?"
    )
    assert events[-1]["type"] == "completed", events

    assert OPEN_TAG not in _tokens(events, "text").lower(), events
    assert "Answer it directly" in _tokens(events, "thinking")
    assert "Paris." in _tokens(events, "text")

    items = await _messages(authenticated_client, pod_id, conversation_id)
    _assert_no_answer_carries_reasoning(items)
    assert [item["text"] for item in items if item["kind"] == "THINKING"] == [
        "General knowledge question. Answer it directly and concisely."
    ]
    answers = [
        item
        for item in items
        if item["role"] == "assistant" and item["kind"] == "TEXT" and item["text"]
    ]
    assert [item["text"] for item in answers] == ["Paris."]


@pytest.mark.asyncio
async def test_an_answer_that_was_only_reasoning_is_not_recorded_as_the_answer(
    authenticated_client, fixed_test_org, e2e_settings, worker
):
    """No answer is better than reasoning wearing an answer's clothes.

    This is the case that poisoned a run's output: an assistant TEXT row is
    stamped `is_final_answer` and published as the run's result, which is what
    a subagent or an agent-as-tool caller reads back.
    """
    del worker
    pod_id, conversation_id = await _agent_and_conversation(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
        [script_inline_reasoning("Thinking, with nothing to show for it.")],
    )

    events = await _send_message(
        authenticated_client, pod_id, conversation_id, "Capital of France?"
    )
    assert events[-1]["type"] == "completed", events

    items = await _messages(authenticated_client, pod_id, conversation_id)
    _assert_no_answer_carries_reasoning(items)
    assert [item["text"] for item in items if item["kind"] == "THINKING"] == [
        "Thinking, with nothing to show for it."
    ]
    assert not [
        item
        for item in items
        if item["role"] == "assistant"
        and item["kind"] == "TEXT"
        and (item["text"] or "").strip()
    ]


@pytest.mark.asyncio
async def test_a_fourth_turn_answers_with_an_answer(
    authenticated_client, fixed_test_org, e2e_settings, worker
):
    """The reported bug, at the depth it was reported at.

    Every turn reasons, so by the fourth the rebuilt history carries three prior
    thoughts -- the depth at which replaying them as ``<think>`` text had
    reliably taught the real model to answer in tags. What is pinned is that
    accumulated thinking never turns into an answer made of reasoning; a
    scripted model has no mind to change, so this cannot catch the model's own
    drift, only the input that caused it.

    One script turn, deliberately: the DSL indexes turns *within a run* (model
    responses since the last real user prompt, see `_current_run_turn_index`),
    so it restarts at every new user message rather than walking down the list.
    """
    del worker
    pod_id, conversation_id = await _agent_and_conversation(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
        [script_inline_reasoning("Recalling what was asked.", "Paris.")],
    )

    for prompt in ("pods?", "tables?", "owner?", "anything else?"):
        events = await _send_message(
            authenticated_client, pod_id, conversation_id, prompt
        )
        assert events[-1]["type"] == "completed", events
        assert OPEN_TAG not in _tokens(events, "text").lower(), prompt

    items = await _messages(authenticated_client, pod_id, conversation_id)
    _assert_no_answer_carries_reasoning(items)

    answers = [
        item["text"]
        for item in items
        if item["role"] == "assistant" and item["kind"] == "TEXT" and item["text"]
    ]
    assert answers == ["Paris."] * 4
    assert [item["text"] for item in items if item["kind"] == "THINKING"] == [
        "Recalling what was asked."
    ] * 4


@pytest.mark.asyncio
async def test_an_ordinary_answer_is_untouched(
    authenticated_client, fixed_test_org, e2e_settings, worker
):
    """The control. Most answers contain no reasoning and must not be rewritten."""
    del worker
    pod_id, conversation_id = await _agent_and_conversation(
        authenticated_client,
        fixed_test_org,
        e2e_settings,
        [script_text("Paris is the capital of France.")],
    )

    events = await _send_message(
        authenticated_client, pod_id, conversation_id, "Capital of France?"
    )
    assert events[-1]["type"] == "completed", events
    assert "Paris is the capital of France." in _tokens(events, "text")

    items = await _messages(authenticated_client, pod_id, conversation_id)
    assert not [item for item in items if item["kind"] == "THINKING"]
    answers = [
        item["text"]
        for item in items
        if item["role"] == "assistant" and item["kind"] == "TEXT" and item["text"]
    ]
    assert answers == ["Paris is the capital of France."]
