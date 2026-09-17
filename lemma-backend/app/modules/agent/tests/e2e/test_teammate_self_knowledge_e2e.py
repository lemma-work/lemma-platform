"""Ask a real teammate, running a real model, the questions it could not answer.

The brief renders correctly -- `test_agent_self_brief_e2e` proves that against a
real pod. This proves the part that actually matters: that a model reading it
answers from it. A section can be present in the prompt and still be ignored,
and the two questions below are the ones the old prompt made unanswerable.

Marked `provider` and `real_llm`, so it is excluded from the default e2e run and
costs nothing until somebody asks for it.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from fastapi import status

from app.modules.agent.tests.e2e.system_lemma_helpers import (
    SYSTEM_LEMMA_SKIP_REASON,
    system_lemma_available,
)

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.provider,
    pytest.mark.real_llm,
    pytest.mark.skipif(not system_lemma_available(), reason=SYSTEM_LEMMA_SKIP_REASON),
]


async def _say(client, pod_id: str, conversation_id: str, text: str) -> str:
    """Send one message, wait for the run, and return what the agent said.

    The reply is read back off the conversation rather than reassembled from the
    token stream: the stream is how a person watches a run, the stored messages
    are what the run produced, and only the second is what this test is about.
    """
    async with client.stream(
        "POST",
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        json={"content": text},
        timeout=180,
    ) as response:
        assert response.status_code == status.HTTP_200_OK, (
            await response.aread()
        ).decode()
        async for line in response.aiter_lines():
            if not line.startswith("data: "):
                continue
            try:
                event = json.loads(line.removeprefix("data: "))
            except json.JSONDecodeError:
                continue
            if event.get("type") in {"completed", "stopped", "error"}:
                outcome = event
                break
        else:
            outcome = {"type": "no terminal event"}

    if outcome.get("type") != "completed":
        # The stream's error text is deliberately generic. The conversation
        # carries the diagnostics from the last run, which is the whole reason
        # they are stored on it -- say them rather than making somebody go and
        # look.
        fetched = await client.get(f"/pods/{pod_id}/conversations/{conversation_id}")
        detail = {
            key: fetched.json().get(key)
            for key in (
                "last_run_status",
                "last_run_error",
                "last_run_error_code",
                "last_run_error_reason",
            )
        }
        raise AssertionError(f"run did not complete: {outcome} / {detail}")

    listed = await client.get(
        f"/pods/{pod_id}/conversations/{conversation_id}/messages"
    )
    assert listed.status_code == status.HTTP_200_OK, listed.text
    messages = listed.json().get("items") or listed.json().get("messages") or []
    spoken = [
        message.get("text") or ""
        for message in messages
        if message.get("role") == "assistant" and message.get("kind") == "TEXT"
    ]
    assert spoken, (
        f"the agent said nothing; got {[(m.get('role'), m.get('kind')) for m in messages]}"
    )
    return spoken[-1]


@pytest.mark.asyncio
async def test_a_teammate_answers_from_its_own_brief(
    authenticated_client, fixed_test_org, configure_workspace_api_url, worker
):
    """Two questions, both unanswerable before this change.

    "What are you called?" had no source at all -- the agent's own name never
    reached any prompt. "What runs on a schedule here?" had one: a tool call the
    agent had to think to make, against a resource the brief never mentioned.
    """
    # `configure_workspace_api_url` starts the per-test sandbox manager. The
    # teammate carries the full batteries-included toolset, WORKSPACE_CLI
    # included, so without a manager to reach the run fails before it says
    # anything -- and the failure reads as a runtime misconfiguration.
    _ = worker
    pod_name = f"Renewals Desk {uuid4().hex[:4]}"
    created = await authenticated_client.post(
        "/pods",
        json={
            "name": pod_name,
            "type": "HYBRID",
            "organization_id": fixed_test_org["id"],
            "description": "Chasing contract renewals before they lapse.",
        },
    )
    assert created.status_code == status.HTTP_201_CREATED, created.text
    pod_id = created.json()["id"]

    scheduled = await authenticated_client.post(
        f"/pods/{pod_id}/schedules",
        json={
            "name": "renewal-sweep",
            "schedule_type": "TIME",
            "agent_name": "POD_DEFAULT",
            "instruction": "Find contracts lapsing in 30 days and flag them.",
            "config": {"cron": "0 9 * * 1-5"},
        },
    )
    assert scheduled.status_code in (
        status.HTTP_200_OK,
        status.HTTP_201_CREATED,
    ), scheduled.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations", json={"title": "Who are you"}
    )
    assert conversation.status_code == status.HTTP_201_CREATED, conversation.text
    conversation_id = conversation.json()["id"]

    name_answer = await _say(
        authenticated_client,
        pod_id,
        conversation_id,
        "What are you called, and what is this workspace for? One short answer.",
    )
    print(f"\n--- asked its name ---\n{name_answer}\n")
    assert pod_name.split()[0].lower() in name_answer.lower(), (
        "the teammate should answer to the pod's name"
    )
    assert "lem" not in name_answer.lower().replace("lemma", ""), (
        "the platform's default responder name must not be introduced as its own"
    )

    work_answer = await _say(
        authenticated_client,
        pod_id,
        conversation_id,
        "Do you have any standing work? Answer from what you already know.",
    )
    print(f"\n--- asked about standing work ---\n{work_answer}\n")
    assert "renewal-sweep" in work_answer.lower(), (
        "its own schedule is in the brief and should not need a lookup"
    )
