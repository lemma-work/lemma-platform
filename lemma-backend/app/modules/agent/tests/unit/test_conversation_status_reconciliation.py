"""A finished run must never leave its conversation running.

`finish_agent_run` is the only place that sees both rows. When the run is
already terminal it has nothing to end — but it used to return a
`conversation_status` it had merely *inferred* from the run row and never
written, so a conversation that had fallen out of step stayed that way forever:
`reconcile_orphaned_agent_runs` keys on `agent_runs.status` alone, so a terminal
run is invisible to it however wrong its conversation is.

Dev saw exactly this — a conversation reporting `status: RUNNING` alongside
`last_run_status: COMPLETED` — and a stuck conversation wedges whatever reads
it, including a schedule run and a workflow step.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository


class _Run:
    """The columns `finish_agent_run` reads and writes on an agent run."""

    def __init__(self, *, conversation_id, status: str) -> None:
        self.id = uuid4()
        self.conversation_id = conversation_id
        self.status = status
        self.error: str | None = None
        self.output_data: object | None = None
        self.finished_at = None


class _Conversation:
    def __init__(self, *, status: str) -> None:
        self.id = uuid4()
        self.status = status
        self.output_data: object | None = None


class _Result:
    def __init__(self, model: object) -> None:
        self._model = model

    def scalar_one_or_none(self) -> object:
        return self._model


class _Session:
    """Just enough session to answer the two reads `finish_agent_run` makes."""

    def __init__(self, run: _Run, conversation: _Conversation) -> None:
        self.run = run
        self.conversation = conversation
        self.flushes = 0

    async def execute(self, statement):
        _ = statement
        return _Result(self.run)

    async def get(self, model, primary_key):
        _ = model, primary_key
        return self.conversation

    async def flush(self) -> None:
        self.flushes += 1


class _Uow:
    def __init__(self, session: _Session) -> None:
        self.session = session

    def collect_events(self, events: list[object]) -> None:
        _ = events


def _rows(
    *,
    run_status: AgentRunStatus,
    conversation_status: ConversationStatus,
) -> tuple[_Run, _Conversation]:
    conversation = _Conversation(status=conversation_status.value)
    run = _Run(conversation_id=conversation.id, status=run_status.value)
    return run, conversation


async def _finish(run: _Run, conversation: _Conversation):
    session = _Session(run, conversation)
    result = await ConversationRepository(_Uow(session)).finish_agent_run(
        agent_run_id=run.id,
        status=AgentRunStatus.COMPLETED,
    )
    return result, session


@pytest.mark.asyncio
async def test_a_finished_run_puts_a_stuck_conversation_back_in_step() -> None:
    """The repair itself: terminal run, conversation still RUNNING."""
    run, conversation = _rows(
        run_status=AgentRunStatus.COMPLETED,
        conversation_status=ConversationStatus.RUNNING,
    )

    result, session = await _finish(run, conversation)

    assert result is not None
    assert conversation.status == ConversationStatus.COMPLETED.value
    assert result.conversation_repaired is True
    # The *run* did not move, and callers key event publishing and usage
    # accounting off `updated` — a repair must not be read as this call having
    # ended the run.
    assert result.updated is False
    assert session.flushes == 1


@pytest.mark.asyncio
async def test_a_waiting_conversation_is_left_alone() -> None:
    """The case that must not be "fixed".

    A run that ends by asking a question leaves its conversation WAITING, and
    that is the correct resting state. Collapsing it to COMPLETED would tear
    down the pause `request_approval` and `ask_user` are built on — the person
    would be asked something the product had already stopped waiting for.
    """
    run, conversation = _rows(
        run_status=AgentRunStatus.COMPLETED,
        conversation_status=ConversationStatus.WAITING,
    )

    result, session = await _finish(run, conversation)

    assert result is not None
    assert conversation.status == ConversationStatus.WAITING.value
    assert result.conversation_repaired is False
    assert session.flushes == 0


@pytest.mark.asyncio
async def test_a_stop_requested_conversation_is_settled() -> None:
    """STOP_REQUESTED is active too: nothing is going to answer it now."""
    run, conversation = _rows(
        run_status=AgentRunStatus.COMPLETED,
        conversation_status=ConversationStatus.STOP_REQUESTED,
    )

    result, _ = await _finish(run, conversation)

    assert result is not None
    assert conversation.status == ConversationStatus.COMPLETED.value
    assert result.conversation_repaired is True


@pytest.mark.asyncio
async def test_an_ordinary_second_finalize_writes_nothing() -> None:
    """Both rows already agree, so the repair must be silent and free."""
    run, conversation = _rows(
        run_status=AgentRunStatus.COMPLETED,
        conversation_status=ConversationStatus.COMPLETED,
    )

    result, session = await _finish(run, conversation)

    assert result is not None
    assert result.updated is False
    assert result.conversation_repaired is False
    assert session.flushes == 0


@pytest.mark.asyncio
async def test_the_pair_invariant_holds_for_every_terminal_run_status() -> None:
    """Whatever the run finished as, the conversation ends up saying so.

    Asserted across all three because the repair reads the *run's* status, not
    the caller's argument — a failed run must not be reconciled to COMPLETED.
    """
    for run_status in (
        AgentRunStatus.COMPLETED,
        AgentRunStatus.FAILED,
        AgentRunStatus.STOPPED,
    ):
        run, conversation = _rows(
            run_status=run_status,
            conversation_status=ConversationStatus.RUNNING,
        )

        result, _ = await _finish(run, conversation)

        assert result is not None
        assert conversation.status == run_status.value, run_status
        assert result.conversation_repaired is True, run_status
