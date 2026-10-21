"""The contracts later workstreams build agent turns on: turn start, its class, the run fact."""

from __future__ import annotations

import json
from uuid import uuid4

from app.core.infrastructure.work import WorkClass
from app.modules.agent.contracts.run_facts import AgentRunFinished
from app.modules.agent.contracts.turns import (
    AgentTurnStart,
    stage_agent_turn,
    turn_class_for,
)
from app.modules.agent.events import work_kinds
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.tests.unit.work_recording import RecordingWorkSession


class _Uow:
    def __init__(self, *, title: str | None = "A title") -> None:
        self.session = RecordingWorkSession(title=title)


def _start(**changes: object) -> AgentTurnStart:
    return AgentTurnStart(
        conversation_id=uuid4(),
        run_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        **changes,  # type: ignore[arg-type]
    )


async def test_stage_agent_turn_writes_the_turn_in_the_callers_transaction() -> None:
    uow = _Uow()
    run = _start()

    await stage_agent_turn(uow, run)  # type: ignore[arg-type]

    turn = uow.session.item(work_kinds.AGENT_TURN)
    assert turn["ns"] == f"pod:{run.pod_id}"
    assert turn["cls"] == "interactive"
    assert turn["dedup_key"] == work_kinds.turn_key(run.run_id)
    assert turn["concurrency_key"] == f"conversation:{run.conversation_id}"
    assert turn["concurrency_limit"] == 1
    assert json.loads(turn["payload"])["run_id"] == str(run.run_id)
    # A conversation that already has a title gets no title job.
    assert uow.session.kinds() == [work_kinds.AGENT_TURN]


async def test_an_untitled_conversation_gets_its_title_item() -> None:
    uow = _Uow(title=None)
    run = _start()

    await stage_agent_turn(uow, run)  # type: ignore[arg-type]

    title = uow.session.item(work_kinds.AGENT_TITLE)
    assert title["dedup_key"] == work_kinds.title_key(run.conversation_id)
    # One at a time: a second title item waits for the first to set the title.
    assert title["concurrency_key"] == work_kinds.title_key(run.conversation_id)
    assert title["cls"] == "automation"


async def test_an_automation_turn_lands_in_the_automation_class() -> None:
    uow = _Uow()

    await stage_agent_turn(uow, _start(origin="schedule"))  # type: ignore[arg-type]

    turn = uow.session.item(work_kinds.AGENT_TURN_AUTOMATION)
    assert turn["cls"] == "automation"


async def test_stage_agent_turn_honours_an_inline_caller() -> None:
    uow = _Uow()
    with suppress_agent_run_enqueue():
        await stage_agent_turn(uow, _start())  # type: ignore[arg-type]
    assert uow.session.items == []


def test_automation_origins_run_in_the_automation_class() -> None:
    assert turn_class_for("schedule") is WorkClass.AUTOMATION
    assert turn_class_for("workflow") is WorkClass.AUTOMATION
    assert turn_class_for("trigger") is WorkClass.AUTOMATION
    assert turn_class_for("chat") is WorkClass.INTERACTIVE
    assert turn_class_for("surface") is WorkClass.INTERACTIVE


def test_the_run_fact_runs_its_subscribers_in_the_pod() -> None:
    pod_id = uuid4()
    fact = AgentRunFinished(
        run_id=uuid4(),
        conversation_id=uuid4(),
        pod_id=pod_id,
        status="COMPLETED",
        conversation_status="COMPLETED",
        origin="chat",
    )
    assert fact.work_namespace() == f"pod:{pod_id}"


def test_the_turn_key_is_the_one_stop_cancels_by() -> None:
    run_id = uuid4()
    assert work_kinds.turn_key(run_id) == f"agent-run:{run_id}"
