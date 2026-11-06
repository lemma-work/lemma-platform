"""The rules an agent turn on the work runtime is held to, without a database.

Where a shutdown may cut a turn (a step boundary), what interrupts a run rather
than ending it, which transaction a turn's end commits in, and what a person
reads when a turn gives up. The behaviour against real Postgres and a real
worker is in ``tests/e2e/test_agent_turn_work_e2e.py``.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from uuid import uuid4

import pytest
from sqlalchemy.exc import OperationalError

from app.core.infrastructure.work import LeaseLost
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
)
from app.modules.agent.events.turn_work import (
    INTERRUPTED_RUN_ERROR,
    TIMED_OUT_RUN_ERROR,
    FencedTerminal,
    dead_error,
)
from app.modules.agent.services.run_metrics import run_is_not_over
from app.modules.agent.services.run_event_pump import StepBoundary, TurnYielded

pytestmark = pytest.mark.unit


def _message(draft: MessageDraft) -> AgentEvent:
    return AgentEvent(type=AgentEventType.MESSAGE, data=draft, agent_run_id=uuid4())


def _call(call_id: str) -> AgentEvent:
    return _message(
        MessageDraft.of_tool_call(tool_name="pod_read_file", tool_call_id=call_id)
    )


def _return(call_id: str) -> AgentEvent:
    return _message(MessageDraft.of_tool_return(tool_call_id=call_id, tool_result={}))


class TestTheStepBoundary:
    def test_the_last_return_of_a_step_is_a_boundary(self) -> None:
        boundary = StepBoundary()
        assert not boundary.observe(_call("a"))
        assert not boundary.observe(_call("b"))
        assert not boundary.observe(_return("a"))
        assert boundary.observe(_return("b"))

    def test_text_and_tokens_are_never_a_boundary(self) -> None:
        boundary = StepBoundary()
        assert not boundary.observe(_message(MessageDraft.of_text("half an answer")))
        token = AgentEvent(type=AgentEventType.TOKEN, data="x", agent_run_id=uuid4())
        assert not boundary.observe(token)

    def test_a_return_for_a_call_still_open_elsewhere_is_not_one(self) -> None:
        boundary = StepBoundary()
        boundary.observe(_call("a"))
        boundary.observe(_call("b"))
        assert not boundary.observe(_return("b"))


class TestWhatInterruptsARun:
    def test_a_cancellation_or_yield_leaves_the_run_running(self) -> None:
        assert run_is_not_over(asyncio.CancelledError())
        assert run_is_not_over(TurnYielded())

    def test_a_lost_lease_belongs_to_the_worker_that_took_it(self) -> None:
        assert run_is_not_over(LeaseLost("item"))

    def test_a_transient_database_failure_is_retried_not_failed(self) -> None:
        class _Orig(Exception):
            sqlstate = "40P01"

        deadlocked = OperationalError("UPDATE agent_runs", {}, _Orig("deadlock"))
        assert run_is_not_over(deadlocked)

    def test_anything_else_ends_the_run(self) -> None:
        assert not run_is_not_over(ValueError("the model said no"))


class _Ctx:
    def __init__(self) -> None:
        self.completed = False
        self.completions = 0

    @asynccontextmanager
    async def complete_work(self):
        self.completions += 1
        yield "fenced"
        self.completed = True


class TestTheTerminalTransaction:
    async def test_the_first_end_commits_with_the_item(self) -> None:
        ctx = _Ctx()
        plain: list[str] = []

        @asynccontextmanager
        async def fallback():
            plain.append("plain")
            yield "plain"

        terminal = FencedTerminal(ctx, fallback)  # type: ignore[arg-type]
        async with terminal() as uow:
            assert uow == "fenced"
        async with terminal() as uow:
            assert uow == "plain"
        assert ctx.completions == 1
        assert plain == ["plain"]

    async def test_a_failed_end_is_still_fenced_the_next_time(self) -> None:
        ctx = _Ctx()

        @asynccontextmanager
        async def fallback():
            yield "plain"

        terminal = FencedTerminal(ctx, fallback)  # type: ignore[arg-type]
        with pytest.raises(RuntimeError):
            async with terminal():
                raise RuntimeError("finalize failed")
        ctx.completed = False
        async with terminal() as uow:
            assert uow == "fenced"
        assert ctx.completions == 2


def test_what_a_person_reads_when_a_turn_gives_up() -> None:
    assert dead_error("max_runtime", None) == TIMED_OUT_RUN_ERROR
    assert dead_error("lease_losses_exhausted", None) == INTERRUPTED_RUN_ERROR
    assert "repeated errors: boom" in dead_error("attempts_exhausted", "boom")
    unavailable = dead_error("unavailable_exhausted", "Unavailable: database")
    assert "unavailable for 30 minutes" in unavailable
    assert "nothing to do with this teammate or its model settings" in unavailable
    assert unavailable != INTERRUPTED_RUN_ERROR
