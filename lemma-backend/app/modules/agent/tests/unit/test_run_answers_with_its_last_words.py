"""A run that completes without naming an answer answers with its last words.

Its output is the result every caller reads -- a workflow step, a channel's
reply, a parent agent -- and an Agent Host turn could reach COMPLETED with its
answer on the record as narration: the host announces a call its adapter never
settled only once the turn is over, after the closing words, and words before
a call are sealed as a step on the way. Which words stand as the answer is read
from the record (``RunMessageWriter.last_words``, pinned against Postgres in
``test_agent_host_last_words_e2e``); these pin when the pump asks, and what it
hands the finalizer.
"""

from __future__ import annotations

from uuid import UUID, uuid7

import pytest

from app.modules.agent.domain.entities import Message
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunStatus,
    ConversationStatus,
    MessageDraft,
)
from app.modules.agent.services.run_event_pump import RunEventPump, RunOutcome
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.services.run_message_writer import RunMessageWriter

_RUN = uuid7()
_WORDS = "The desk is set up and ready for bills."


def _narration() -> Message:
    """Closing words sealed as a step on the way, as the Agent Host seals them."""
    return Message.from_draft(
        MessageDraft.of_text(
            _WORDS,
            metadata={
                "agent_host_object_id": "msg-2",
                "is_final_answer": False,
                "is_intermediate_assistant_message": True,
            },
        ),
        conversation_id=uuid7(),
        sequence=7,
        agent_run_id=_RUN,
    )


class _OnRecord(RunMessageWriter):
    """The writer, with the run's last words in hand rather than in Postgres."""

    def __init__(self, words: Message | None) -> None:
        super().__init__(uow_factory=_no_database)
        self.words = words

    async def last_words(self, agent_run_id: UUID) -> Message | None:
        assert agent_run_id == _RUN
        return self.words


def _no_database():
    raise AssertionError("the last words are in memory")


class _Finalizer:
    def __init__(self) -> None:
        self.finished: list[dict[str, object]] = []

    async def finish(self, **kwargs: object) -> None:
        self.finished.append(kwargs)


async def _end(
    writer: RunMessageWriter, event_type: AgentEventType, outcome: RunOutcome
) -> dict[str, object]:
    finalizer = _Finalizer()
    terminal = await RunEventPump(writer, finalizer).handle(
        event=AgentEvent(type=event_type, data={}, agent_run_id=_RUN),
        run=RunIdentity(conversation_id=uuid7(), agent_run_id=_RUN),
        outcome=outcome,
    )
    assert terminal
    (finished,) = finalizer.finished
    return finished


@pytest.mark.asyncio
async def test_a_completed_run_that_named_no_answer_ends_on_its_last_words() -> None:
    words = _narration()
    outcome = RunOutcome()

    finished = await _end(_OnRecord(words), AgentEventType.COMPLETED, outcome)

    assert finished["status"] is AgentRunStatus.COMPLETED
    # The rule for any answer: the text, though the row says it was a step.
    assert finished["output_data"] == {"answer": _WORDS}
    assert finished["answer"] is words
    # Kept on the outcome too: the run's span records what it answered.
    assert outcome.output_data == {"answer": _WORDS}


@pytest.mark.asyncio
async def test_a_completed_run_with_no_last_words_still_ends_without_an_answer() -> (
    None
):
    finished = await _end(_OnRecord(None), AgentEventType.COMPLETED, RunOutcome())

    assert finished["status"] is AgentRunStatus.COMPLETED
    assert finished["output_data"] is None
    assert finished["answer"] is None


@pytest.mark.asyncio
async def test_a_run_that_named_its_answer_keeps_it() -> None:
    outcome = RunOutcome(output_data={"answer": "What the run said it was."})

    finished = await _end(_OnRecord(_narration()), AgentEventType.COMPLETED, outcome)

    assert finished["output_data"] == {"answer": "What the run said it was."}
    assert finished["answer"] is None


@pytest.mark.parametrize(
    ("event_type", "final_status"),
    [
        pytest.param(AgentEventType.STOPPED, None, id="stopped"),
        pytest.param(
            AgentEventType.COMPLETED,
            ConversationStatus.FAILED,
            id="final-answer-said-failed",
        ),
        pytest.param(
            AgentEventType.COMPLETED,
            ConversationStatus.WAITING,
            id="final-answer-said-waiting",
        ),
    ],
)
@pytest.mark.asyncio
async def test_a_run_that_ended_some_other_way_is_given_no_answer(
    event_type: AgentEventType, final_status: ConversationStatus | None
) -> None:
    outcome = RunOutcome(final_status=final_status)

    finished = await _end(_OnRecord(_narration()), event_type, outcome)

    assert finished["output_data"] is None
    assert finished["answer"] is None
