"""The run log, through real turns against Postgres.

Each finished model step of an in-process run appends what it added to the
model's history to ``agent_run_steps``, as pydantic-ai serialized it; the next
run replays it instead of rebuilding from message rows. A conversation from
before the log takes its past in from the rows once, and from the log after.
"""

from __future__ import annotations

import json
from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.run_log import RunStepDraft, RunStepKind
from app.modules.agent.infrastructure.db.run_steps import RunSteps
from app.modules.agent.tests.e2e.turn_work_support import (
    TurnPod,
    drain,
    rows,
    turn_pod,
    turn_registry,
    write,
)
from app.modules.test_support.e2e.scripted_model import script_text, script_tool_call

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


@pytest.fixture
async def pod(authenticated_client, fixed_test_org) -> TurnPod:
    return await turn_pod(authenticated_client, fixed_test_org["id"], toolsets=["POD"])


async def _steps(conversation: str) -> list[tuple[int, str, str | None, list[dict]]]:
    found = await rows(
        "SELECT seq, kind, run_id, messages FROM agent_run_steps "
        "WHERE conversation_id = :id ORDER BY seq",
        id=UUID(conversation),
    )
    return [
        (seq, kind, str(run_id) if run_id else None, json.loads(messages))
        for seq, kind, run_id, messages in found
    ]


def _user_texts(messages: list[dict]) -> list[str]:
    return [
        part["content"]
        for message in messages
        if message["kind"] == "request"
        for part in message["parts"]
        if part["part_kind"] == "user-prompt" and isinstance(part["content"], str)
    ]


async def _turn(pod: TurnPod, conversation: str, content: str) -> str:
    run_id = (await pod.append(conversation, content))["agent_run_id"]
    await drain(turn_registry())
    assert await pod.run_status(run_id) == "COMPLETED"
    return run_id


async def test_each_step_is_logged_and_the_next_run_replays_it(pod: TurnPod) -> None:
    conversation = await pod.conversation(
        [
            script_tool_call("pod_tables", {}, tool_call_id="tables-1"),
            script_text("Done."),
        ]
    )
    first = await _turn(pod, conversation, "List the tables.")
    after_first = await _steps(conversation)
    second = await _turn(pod, conversation, "Thanks.")
    steps = await _steps(conversation)

    # The first run's steps are where it left them, byte for byte.
    assert steps[: len(after_first)] == after_first
    assert {run for _, _, run, _ in after_first} == {first}
    logged = [message for *_, messages in after_first for message in messages]
    assert [message["kind"] for message in logged] == [
        "request",
        "response",
        "request",
        "response",
    ]
    # The second run took in only its own turn: nothing of the first again.
    added = [
        message
        for _, _, run, messages in steps
        if run == second
        for message in messages
    ]
    texts = _user_texts(added)
    assert any("Thanks." in text for text in texts)
    assert not any("List the tables." in text for text in texts)
    # The answer each run closed on is logged once: its row, written after
    # the step, is carried by that step rather than taken in again.
    # Each run logged the two responses its model made, and no copy of the
    # first run's answer.
    for run in (first, second):
        said = [
            message
            for _, _, by, messages in steps
            if by == run
            for message in messages
            if message["kind"] == "response"
        ]
        assert [message["model_name"] is not None for message in said] == [
            True,
            True,
        ], f"run {run} logged a response its model never sent"
    # Every row the runs wrote, or took in, is carried by a step.
    uncarried = await rows(
        "SELECT role, kind, text, agent_run_id FROM agent_messages "
        "WHERE conversation_id = :id AND run_step_seq IS NULL",
        id=UUID(conversation),
    )
    assert uncarried == []


async def test_a_conversation_from_before_the_log_takes_its_past_in_once(
    pod: TurnPod,
) -> None:
    conversation = await pod.conversation([script_text("Noted.")])
    await _turn(pod, conversation, "Remember the blue one.")
    # As a conversation that predates the log: rows, and no steps.
    await write(
        "DELETE FROM agent_run_steps WHERE conversation_id = :id", id=UUID(conversation)
    )
    await write(
        "UPDATE agent_messages SET run_step_seq = NULL WHERE conversation_id = :id",
        id=UUID(conversation),
    )

    second = await _turn(pod, conversation, "Which one?")
    fallback = [
        m for _, _, run, ms in await _steps(conversation) if run == second for m in ms
    ]
    assert any("Remember the blue one." in text for text in _user_texts(fallback))

    third = await _turn(pod, conversation, "And now?")
    steps = await _steps(conversation)
    taken = [m for _, _, run, ms in steps if run == third for m in ms]
    assert not any("Remember the blue one." in text for text in _user_texts(taken))
    assert (
        sum(
            "Remember the blue one." in text
            for *_, messages in steps
            for text in _user_texts(messages)
        )
        == 1
    )


async def test_a_checkpoint_replaces_the_steps_before_it(pod: TurnPod) -> None:
    conversation = await pod.conversation([script_text("Fine.")])
    await _turn(pod, conversation, "Hello.")
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    summary = json.dumps([{"parts": [], "kind": "request"}])
    async with uow_factory() as uow:
        seq = await RunSteps(uow).append(
            conversation_id=UUID(conversation),
            run_id=None,
            draft=RunStepDraft(kind=RunStepKind.COMPACTION, messages=summary),
        )
        await RunSteps(uow).append(
            conversation_id=UUID(conversation),
            run_id=None,
            draft=RunStepDraft(
                kind=RunStepKind.NOTICE, messages="[]", covers=(uuid4(),)
            ),
        )
        await uow.commit()
    async with uow_factory() as uow:
        replay = await RunSteps(uow).replay(UUID(conversation))
    assert [(step.seq, step.kind) for step in replay] == [
        (seq, RunStepKind.COMPACTION),
        (seq + 1, RunStepKind.NOTICE),
    ]
    assert replay[0].messages == summary
    assert [seq_ for seq_, *_ in await _steps(conversation)] == [seq, seq + 1]
