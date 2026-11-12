"""A place in Lemma is named once, by ``in_lemma``, and stored as named.

Steering compares where a waiting message was said with where its run was
asked. Built twice -- once in Python for the scope a follow-up answers, once in
SQL for the claim -- the two names had to agree character for character, and a
change to either would have quietly stopped every group thread steering. The
name is made when the message and the run are written, and the database only
compares what was stored.
"""

from __future__ import annotations

from uuid import uuid4

from sqlalchemy.dialects import postgresql

from app.modules.agent.domain.run_places import (
    ASKED_IN_PLACE_KEY,
    AnswerScope,
    in_lemma,
    stored_place,
)
from app.modules.agent.domain.run_state import RunState
from app.modules.agent.infrastructure.db import queued_messages
from app.modules.agent.infrastructure.models.typed_state import run_state_values


def _sql(expression) -> str:
    return str(
        expression.compile(
            dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}
        )
    )


def test_the_database_compares_a_stored_place_and_builds_none():
    said_at = _sql(queued_messages._message_place())
    assert ASKED_IN_PLACE_KEY in said_at
    assert "concat" not in said_at.lower()
    assert "||" not in said_at


def test_a_run_stores_the_place_its_scope_names():
    thread = uuid4()
    for state in (
        RunState(asked_in_lemma=True, asked_in_thread=thread),
        RunState(asked_in_lemma=True),
    ):
        stored = stored_place(run_state_values(state)["run_metadata"])
        assert stored == in_lemma(state.asked_in_thread)
        assert stored == AnswerScope.of_run(state.privacy).place


def test_a_run_asked_at_no_lemma_place_stores_none():
    assert "run_metadata" not in run_state_values(RunState())
