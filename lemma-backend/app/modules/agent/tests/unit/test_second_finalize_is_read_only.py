"""Finishing a run that already finished reports, and writes nothing.

The run's own finish wrote its conversation's status in the same transaction,
so a second finalize -- the worker and the orphan sweep both reaching one run --
has nothing to put right. It reports the conversation as stored: in particular
a WAITING conversation stays WAITING, the resting state a pause is built on.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
)
from app.modules.agent.infrastructure.db.runs import Runs


class _Run:
    """The columns `finish_agent_run` reads and writes on an agent run."""

    def __init__(self, *, conversation_id, status: str) -> None:
        self.id = uuid4()
        self.conversation_id = conversation_id
        self.status = status
        self.error: str | None = None
        self.output_data: object | None = None
        self.finished_at = None
        self.run_metadata: dict[str, object] | None = None


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

    def one_or_none(self) -> object:
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

    async def scalar(self, statement):
        _ = statement
        return self.conversation.status

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
    result = await Runs(_Uow(session)).finish_agent_run(
        agent_run_id=run.id,
        status=AgentRunStatus.COMPLETED,
    )
    return result, session


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "stored",
    [
        ConversationStatus.COMPLETED,
        ConversationStatus.WAITING,
        ConversationStatus.FAILED,
    ],
)
async def test_a_second_finalize_reports_what_is_stored_and_writes_nothing(
    stored: ConversationStatus,
) -> None:
    run, conversation = _rows(
        run_status=AgentRunStatus.COMPLETED, conversation_status=stored
    )

    result, session = await _finish(run, conversation)

    assert result is not None
    # Callers key event publishing and usage accounting off `updated`.
    assert result.updated is False
    assert result.status is AgentRunStatus.COMPLETED
    assert result.conversation_status is stored
    assert conversation.status == stored.value
    assert session.flushes == 0
