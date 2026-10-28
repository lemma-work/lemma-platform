"""Consecutive runs of a conversation share their history as a cached prefix.

The history is most of every request, and a provider only reuses the part of a
request that is byte-identical to one it has seen. Collapsing the oldest full
run on every new run, and dropping the oldest carried run on every run past the
cap, rewrote the history near its start each time -- so the first request of
every run re-read nearly all of it. Elision and dropping now move in whole
blocks, so the history only grows at its end between two block boundaries.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.modules.agent.domain.entities import AgentRun, Message
from app.modules.agent.domain.run_state import RunState
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentRuntimeConfig,
    MessageKind,
    MessageRole,
)
from app.modules.agent.domain.runtime_history import (
    ELISION_BLOCK_RUNS,
    STOPPED_BY_MEMBER,
    bound_runtime_history,
    select_runtime_history,
)

pytestmark = pytest.mark.unit

_BASE = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _conversation(run_count: int) -> list[AgentRun]:
    """Runs of six messages each: a request, four steps, an answer."""
    conversation_id = uuid4()
    runs: list[AgentRun] = []
    for run_index in range(run_count):
        run_id = uuid4()
        runs.append(
            AgentRun(
                id=run_id,
                conversation_id=conversation_id,
                agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
                started_at=_BASE + timedelta(minutes=run_index),
                messages=[
                    Message(
                        conversation_id=conversation_id,
                        sequence=run_index * 100 + index,
                        agent_run_id=run_id,
                        role=MessageRole.USER if index == 0 else MessageRole.ASSISTANT,
                        kind=MessageKind.TEXT,
                        text=f"run {run_index} message {index}",
                    )
                    for index in range(6)
                ],
            )
        )
    return runs


def _sent(runs: list[AgentRun]) -> list[tuple[UUID | None, str, str | None]]:
    """What a run with this much conversation behind it is shown, as the model
    reads it: synthetic notices get fresh ids every time, so ids are left out."""
    bounded, dropped = bound_runtime_history(runs)
    return [
        (message.agent_run_id, message.role.value, message.text)
        for message in select_runtime_history(bounded, already_dropped=dropped)
    ]


def _shares_its_predecessors_history(runs: list[AgentRun], run_count: int) -> bool:
    before = _sent(runs[:run_count])
    after = _sent(runs[: run_count + 1])
    return after[: len(before)] == before


@pytest.mark.parametrize("run_count", [7, 8, 65])
def test_the_next_run_starts_with_this_runs_whole_history(run_count: int) -> None:
    runs = _conversation(run_count + 1)

    assert _shares_its_predecessors_history(runs, run_count)


def test_the_history_is_rewritten_only_at_block_boundaries() -> None:
    """Some rewrite is unavoidable -- that is elision -- but once a block, not
    once a run, and dropping past the cap lands on the same boundaries."""
    runs = _conversation(90)

    rewritten = [
        run_count
        for run_count in range(1, 89)
        if not _shares_its_predecessors_history(runs, run_count)
    ]

    assert rewritten
    assert all((run_count + 1) % ELISION_BLOCK_RUNS == 0 for run_count in rewritten)


def test_a_stopped_run_is_closed_before_what_was_said_after_stop() -> None:
    """A line typed after Stop rides on the stopped run and is what the next
    run answers, so the stop is said between the work and that line."""
    [run] = _conversation(1)
    run.status = AgentRunStatus.STOPPED
    run.state = RunState(stop_requested_after_sequence=5)
    run.messages.append(
        Message(
            conversation_id=run.conversation_id,
            sequence=6,
            agent_run_id=run.id,
            role=MessageRole.USER,
            kind=MessageKind.TEXT,
            text="what is 2+2?",
        )
    )

    texts = [message.text for message in select_runtime_history([run])]

    assert texts[-3:] == ["run 0 message 5", STOPPED_BY_MEMBER, "what is 2+2?"]
