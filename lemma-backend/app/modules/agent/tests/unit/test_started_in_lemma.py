"""Which runs may use their person's own machine: ``domain/started_in_lemma``.

The rule reads the run's kind, as ``domain/run_kind`` says every per-turn
decision should, not the kind the conversation was opened as.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.run_kind import RunKind
from app.modules.agent.domain.started_in_lemma import started_in_lemma
from app.modules.agent.domain.value_objects import ConversationType

pytestmark = pytest.mark.unit

PERSON = uuid4()


def _conversation(**fields) -> Conversation:
    return Conversation(user_id=PERSON, pod_id=uuid4(), **fields)


def test_the_persons_own_chat_in_lemma_reaches_their_machine() -> None:
    assert started_in_lemma(_conversation(), PERSON, kind=RunKind.CHAT)


@pytest.mark.parametrize(
    "kind",
    [kind for kind in RunKind if kind not in (RunKind.CHAT, RunKind.SUBAGENT)],
)
def test_no_other_kind_of_turn_does(kind: RunKind) -> None:
    assert not started_in_lemma(_conversation(), PERSON, kind=kind)


def test_a_person_writing_into_a_schedules_conversation_is_their_own_turn() -> None:
    """The conversation was opened as a task; the turn is the person's."""
    task = _conversation(type=ConversationType.TASK)

    assert started_in_lemma(task, PERSON, kind=RunKind.CHAT)
    assert not started_in_lemma(task, PERSON, kind=RunKind.SCHEDULE)


def test_a_schedules_turn_in_a_chat_conversation_is_not() -> None:
    assert not started_in_lemma(_conversation(), PERSON, kind=RunKind.SCHEDULE)


def test_a_sub_agent_answers_as_the_run_that_spawned_it() -> None:
    child = _conversation(parent_run_id=uuid4())

    assert started_in_lemma(
        child, PERSON, kind=RunKind.SUBAGENT, parent_kind=RunKind.CHAT
    )
    assert not started_in_lemma(
        child, PERSON, kind=RunKind.SUBAGENT, parent_kind=RunKind.SCHEDULE
    )
    assert not started_in_lemma(child, PERSON, kind=RunKind.SUBAGENT)


def test_only_the_conversations_person_and_only_where_nobody_else_hears() -> None:
    assert not started_in_lemma(_conversation(), uuid4(), kind=RunKind.CHAT)
    assert not started_in_lemma(
        _conversation(contact_id=uuid4()), PERSON, kind=RunKind.CHAT
    )
    assert not started_in_lemma(
        _conversation(channel_id=uuid4()), PERSON, kind=RunKind.CHAT
    )


def test_a_conversation_with_no_run_yet_reaches_nothing() -> None:
    assert not started_in_lemma(_conversation(), PERSON, kind=None)
