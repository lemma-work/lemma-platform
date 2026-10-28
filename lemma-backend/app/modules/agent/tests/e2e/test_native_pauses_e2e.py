"""Pauses as deferred calls, through real turns: every pause of a step, and its
siblings, and the run that resumes once all of them are answered.

A step that asks two questions and reads the pod's tables in one response used
to end on the first question: the second was cancelled mid-body and closed as
"paused before the tool returned", and the table read with it. Now each pausing
call writes its pause, the read returns, and the run waits on both questions.
"""

from __future__ import annotations

import json
from uuid import UUID

import pytest

from app.modules.agent.tests.e2e.turn_work_support import (
    TurnPod,
    drain,
    rows,
    scalar,
    turn_pod,
    turn_registry,
)
from app.modules.test_support.e2e.scripted_model import script_text

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


def _question(text: str, header: str) -> dict[str, object]:
    return {
        "question": text,
        "header": header,
        "options": [{"label": "Yes"}, {"label": "No"}],
    }


def _two_questions_and_a_read() -> dict[str, object]:
    return {
        "text": None,
        "tool_calls": [
            {
                "tool_name": "ask_user",
                "args": {"questions": [_question("Keep the old rows?", "Rows")]},
                "tool_call_id": "ask-rows",
            },
            {
                "tool_name": "ask_user",
                "args": {"questions": [_question("Email the team?", "Email")]},
                "tool_call_id": "ask-email",
            },
            {"tool_name": "pod_tables", "args": {}, "tool_call_id": "read-tables"},
        ],
    }


@pytest.fixture
async def pod(authenticated_client, fixed_test_org) -> TurnPod:
    return await turn_pod(
        authenticated_client,
        fixed_test_org["id"],
        toolsets=["POD", "USER_INTERACTION"],
    )


async def _answer(pod: TurnPod, conversation: str, call_id: str, answer: str) -> None:
    response = await pod.client.post(
        f"/pods/{pod.pod_id}/conversations/{conversation}/approvals/{call_id}/decision",
        json={"decision": "APPROVE_ONCE", "response": {"answers": {"q": answer}}},
    )
    assert response.status_code == 200, response.text


async def test_two_pauses_and_a_sibling_in_one_step_then_one_resume(
    pod: TurnPod,
) -> None:
    conversation = await pod.conversation(
        [_two_questions_and_a_read(), script_text("Kept the rows; emailing.")]
    )
    paused = (await pod.append(conversation, "Tidy up."))["agent_run_id"]
    await drain(turn_registry())

    assert await pod.run_status(paused) == "COMPLETED"
    assert (
        await scalar(
            "SELECT status FROM agent_conversations WHERE id = :id",
            id=UUID(conversation),
        )
        == "WAITING"
    )
    items = await pod.messages(conversation)
    returns = {
        item["tool_call_id"]: item["tool_result"]
        for item in items
        if item["kind"] == "TOOL_RETURN"
    }
    # The read beside the questions ran to the end; neither question was
    # closed as never returned.
    assert set(returns) == {"read-tables"}
    assert returns["read-tables"]["success"] is True
    approvals = await pod.client.get(
        f"/pods/{pod.pod_id}/conversations/{conversation}/approvals"
    )
    assert {item["tool_call_id"] for item in approvals.json()["items"]} == {
        "ask-rows",
        "ask-email",
    }

    # One answer is not enough to resume; both are.
    await _answer(pod, conversation, "ask-rows", "Yes")
    await drain(turn_registry())
    runs = await rows(
        "SELECT id FROM agent_runs WHERE conversation_id = :id", id=UUID(conversation)
    )
    assert len(runs) == 1, "a run resumed with a question still open"
    await _answer(pod, conversation, "ask-email", "No")
    await drain(turn_registry())

    resumed = await rows(
        "SELECT id, status FROM agent_runs WHERE conversation_id = :id AND id <> :paused",
        id=UUID(conversation),
        paused=UUID(paused),
    )
    assert [status for _, status in resumed] == ["COMPLETED"]
    steps = await rows(
        "SELECT kind, messages FROM agent_run_steps "
        "WHERE conversation_id = :id AND run_id = :run ORDER BY seq",
        id=UUID(conversation),
        run=resumed[0][0],
    )
    answered = {
        part["tool_call_id"]: part["content"]
        for _, messages in steps
        for message in json.loads(messages)
        if message["kind"] == "request"
        for part in message["parts"]
        if part["part_kind"] == "tool-return"
    }
    assert answered["ask-rows"]["answers"] == {"q": "Yes"}
    assert answered["ask-email"]["answers"] == {"q": "No"}


async def test_a_run_out_of_allowance_waits_and_carries_on_with_no_prompt(
    pod: TurnPod, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The budget pause stops the run before its next request, with no
    exception; approved, the next run re-sends that request -- no new prompt --
    and the model answers it."""
    from app.modules.agent.config import agent_settings

    monkeypatch.setattr(agent_settings, "agent_run_budget_model_requests", 1)
    conversation = await pod.conversation(
        [
            {
                "text": None,
                "tool_calls": [
                    {"tool_name": "pod_tables", "args": {}, "tool_call_id": "read-1"}
                ],
            },
            script_text("There are no tables yet."),
        ]
    )
    paused = (await pod.append(conversation, "What tables are there?"))["agent_run_id"]
    await drain(turn_registry())

    approvals = await pod.client.get(
        f"/pods/{pod.pod_id}/conversations/{conversation}/approvals"
    )
    (card,) = approvals.json()["items"]
    monkeypatch.setattr(agent_settings, "agent_run_budget_model_requests", 10)
    decided = await pod.client.post(
        f"/pods/{pod.pod_id}/conversations/{conversation}"
        f"/approvals/{card['tool_call_id']}/decision",
        json={"decision": "APPROVE_ONCE"},
    )
    assert decided.status_code == 200, decided.text
    await drain(turn_registry())

    resumed = await rows(
        "SELECT status FROM agent_runs WHERE conversation_id = :id AND id <> :paused",
        id=UUID(conversation),
        paused=UUID(paused),
    )
    assert [status for (status,) in resumed] == ["COMPLETED"]
    texts = [
        item["text"]
        for item in await pod.messages(conversation)
        if item["kind"] == "TEXT" and item["role"] == "assistant"
    ]
    assert texts[-1] == "There are no tables yet."
    user_turns = [
        item
        for item in await pod.messages(conversation)
        if item["kind"] == "TEXT" and item["role"] == "user"
    ]
    assert len(user_turns) == 1, "carrying on added no prompt"
