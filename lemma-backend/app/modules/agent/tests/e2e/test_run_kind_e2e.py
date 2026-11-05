"""A run's kind is the one its start site gave it, stored on the run.

The conversation says what it was opened for; each run says who it is for. A
person writing into a schedule's conversation starts a person's turn: counted,
queued and remembered as one, not as the schedule.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.modules.agent.tests.e2e.turn_work_support import (
    TurnPod,
    drain,
    rows,
    scalar,
    turn_pod,
    turn_registry,
    write,
)
from app.modules.test_support.e2e.scripted_model import script_text

pytestmark = [pytest.mark.e2e, pytest.mark.drives_work]


@pytest.fixture
async def pod(authenticated_client, fixed_test_org) -> TurnPod:
    return await turn_pod(authenticated_client, fixed_test_org["id"], toolsets=["POD"])


async def _kind(run_id: str) -> str:
    return await scalar("SELECT kind FROM agent_runs WHERE id = :id", id=UUID(run_id))


async def test_a_person_in_a_schedules_conversation_starts_their_own_turn(
    pod: TurnPod,
) -> None:
    conversation = await pod.conversation([script_text("Noted.")])
    # As a schedule opened it.
    await write(
        "UPDATE agent_conversations SET origin = 'SCHEDULE' WHERE id = :id",
        id=UUID(conversation),
    )

    run_id = (await pod.append(conversation, "And tomorrow?"))["agent_run_id"]
    queued = await rows(
        "SELECT kind FROM work_items WHERE dedup_key = :key",
        key=f"agent-run:{run_id}",
    )
    await drain(turn_registry())

    assert await _kind(run_id) == "CHAT"
    # In a person's lane, not behind the pod's automation.
    assert [kind for (kind,) in queued] == ["agent.turn"]
    assert await pod.run_status(run_id) == "COMPLETED"


async def test_a_chat_turn_is_a_chat(pod: TurnPod) -> None:
    conversation = await pod.conversation([script_text("Hi.")])
    run_id = (await pod.append(conversation, "Hello."))["agent_run_id"]
    await drain(turn_registry())
    assert await _kind(run_id) == "CHAT"
