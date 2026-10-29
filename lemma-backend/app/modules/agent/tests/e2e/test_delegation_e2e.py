"""Delegation against Postgres: the six sub-agent scenarios, without a model.

The parent's ``delegate`` call runs as the harness runs it (the pause signal
on), its children are finished by hand -- which is all the wait, the wake and
the delivery look at -- and each finish is followed by what its follow-up
items do. subagents.md §5 names the scenarios:

- E1 parallel research: one call, three children, one wake with all three.
- E2 multi-file build: the children are sub-agent turns in the background
  lane, told to keep to a directory of their own, and keep ``wait_for``.
- E3 data work: each report comes back in its child's words, not a pointer
  (E1's reports; the numbers themselves need a model).
- E4 failure: a failed child's report says it failed and why.
- E5 question relay: a child that asks wakes the parent early, with the
  question.
- E6 fire and forget, stop and limits: one delivery for all, Stop stops every
  child, and a run past its cap is told so.
"""

from __future__ import annotations

from uuid import UUID

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.delegation import MAX_CHILDREN_PER_RUN
from app.modules.agent.domain.state_kinds import PauseKind
from app.modules.agent.domain.subagent_results import RESULT_SOURCE
from app.modules.agent.domain.tools import AgentInputRequired
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    MessageKind,
    MessageRole,
)
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.agent.infrastructure.tools.subagents import delegate as delegation
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.services.subagent_result_delivery import SubagentResultDelivery
from app.modules.agent.services.wait_wake_service import AgentWaitService
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    Seeded,
    finish,
    messages,
    pausing_call,
    running_run,
    runs,
    seeded_conversation,
    stop_conversation,
    uow_factory,
)

pytestmark = [pytest.mark.e2e]


def _as_parent(parent: Seeded, run_id: UUID, call_id: str) -> ToolContext:
    return ToolContext(
        user_id=parent.user_id,
        pod_id=parent.pod_id,
        conversation_id=parent.conversation_id,
        workload_id=parent.agent_id,
        agent_name=parent.agent_name,
        agent_run_id=run_id,
        tool_call_id=call_id,
        supports_pause_signal=True,
    )


def _request(*briefs: str, wait: str = "all") -> delegation.DelegateRequest:
    return delegation.DelegateRequest(
        tasks=[delegation.DelegateTask(brief=brief) for brief in briefs],
        wait=wait,  # type: ignore[arg-type]
    )


async def _delegate_and_wait(
    parent: Seeded, *briefs: str
) -> tuple[UUID, str, list[UUID]]:
    """The parent's run delegates ``briefs`` and pauses on them."""
    run_id = await running_run(parent)
    call_id = await pausing_call(
        parent, run_id, tool_name="delegate", tool_args={"tasks": []}
    )
    with suppress_agent_run_enqueue(), pytest.raises(AgentInputRequired) as paused:
        await delegation.delegate(
            _as_parent(parent, run_id, call_id), _request(*briefs)
        )
    assert paused.value.kind == "delegate"
    await finish(run_id, conversation_status=ConversationStatus.WAITING)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        (wait,) = await Pauses(uow).pending(
            parent.conversation_id, kinds=(PauseKind.WAIT,)
        )
    assert wait.wait_spec is not None
    children = [UUID(ref) for ref in wait.wait_spec.targets]
    assert len(children) == len(briefs)
    return run_id, call_id, children


async def _child_ends(
    child_run: UUID,
    *,
    answer: str | None = None,
    status: AgentRunStatus = AgentRunStatus.COMPLETED,
    error: str | None = None,
) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await Runs(uow).finish_agent_run(
            agent_run_id=child_run,
            status=status,
            error=error,
            output_data={"answer": answer} if answer is not None else None,
        )
        await uow.commit()


async def _resolve(child_run: UUID) -> bool:
    """What the child's ``agent.wait.resolve_child`` item does."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        run = await Runs(uow).get_agent_run(child_run)
    assert run is not None
    with suppress_agent_run_enqueue():
        async with uow_factory()() as uow:
            woke = await AgentWaitService(uow).resolve_child(
                child_run_id=child_run, child_conversation_id=run.conversation_id
            )
            await uow.commit()
    return woke


async def _returns(parent: Seeded, call_id: str) -> list[dict[str, object]]:
    return [
        message.tool_result
        for message in await messages(parent.conversation_id)
        if message.kind is MessageKind.TOOL_RETURN and message.tool_call_id == call_id
    ]


async def test_e1_three_children_wake_the_parent_once_with_every_report(
    scenario,
) -> None:
    parent = await seeded_conversation(scenario)
    _, call_id, children = await _delegate_and_wait(
        parent, "Vendor A pricing.", "Vendor B pricing.", "Vendor C pricing."
    )

    for n, child in enumerate(children):
        await _child_ends(child, answer=f"Vendor {'ABC'[n]}: $10.")
        woke = await _resolve(child)
        # Not until the last one: one wake, not three.
        assert woke is (n == len(children) - 1)

    (woke,) = await _returns(parent, call_id)
    assert woke["woke_because"] == "TARGET_FINISHED"
    results = woke["results"]
    assert isinstance(results, list) and len(results) == 3
    assert {item["report"] for item in results} == {
        "Vendor A: $10.",
        "Vendor B: $10.",
        "Vendor C: $10.",
    }
    # The wake started the one resume run.
    assert len(await runs(parent.conversation_id)) == 2


async def test_e2_children_run_as_background_sub_agent_turns(scenario) -> None:
    parent = await seeded_conversation(scenario)
    _, _, children = await _delegate_and_wait(
        parent, "Build the API in /work/todo/api.", "Build the UI in /work/todo/ui."
    )
    async with create_uow_from_session_maker(async_session_maker) as uow:
        found = [await Runs(uow).get_agent_run(child) for child in children]
    assert all(run is not None and run.state.kind.value == "subagent" for run in found)


async def test_e4_a_failed_child_is_reported_failed_with_its_reason(scenario) -> None:
    parent = await seeded_conversation(scenario)
    _, call_id, (ok, broken) = await _delegate_and_wait(parent, "One.", "Two.")
    await _child_ends(ok, answer="Done.")
    await _resolve(ok)
    await _child_ends(broken, status=AgentRunStatus.FAILED, error="tool kept failing")
    await _resolve(broken)

    (woke,) = await _returns(parent, call_id)
    by_status = {item["status"]: item for item in woke["results"]}
    assert by_status["FAILED"]["error"] == "tool kept failing"
    assert by_status["COMPLETED"]["report"] == "Done."


async def test_e5_a_child_that_asks_wakes_the_parent_with_its_question(
    scenario,
) -> None:
    parent = await seeded_conversation(scenario)
    _, call_id, (asking, working) = await _delegate_and_wait(
        parent, "Needs an account id.", "Plain work."
    )
    child = (await runs_of(asking)).conversation_id
    seeded_child = Seeded(
        pod_id=parent.pod_id,
        user_id=parent.user_id,
        agent_id=parent.agent_id,
        agent_name=parent.agent_name,
        conversation_id=child,
    )
    await pausing_call(
        seeded_child,
        asking,
        tool_name="ask_user",
        tool_args={"questions": ["Which account id?"]},
    )
    await finish(asking, conversation_status=ConversationStatus.WAITING)
    assert await _resolve(asking)

    (woke,) = await _returns(parent, call_id)
    assert woke["woke_because"] == "TARGET_NEEDS_INPUT"
    statuses = {item["status"] for item in woke["results"]}
    assert statuses == {"NEEDS_INPUT", "RUNNING"}
    del working


async def runs_of(run_id: UUID):
    async with create_uow_from_session_maker(async_session_maker) as uow:
        run = await Runs(uow).get_agent_run(run_id)
    assert run is not None
    return run


async def test_e6_fire_and_forget_results_arrive_as_one_message(scenario) -> None:
    parent = await seeded_conversation(scenario)
    run_id = await running_run(parent)
    with suppress_agent_run_enqueue():
        started = await delegation.delegate(
            _as_parent(parent, run_id, "call-bg"),
            _request("A.", "B.", "C.", wait="none"),
        )
    await finish(run_id)
    children = [UUID(str(item["run_id"])) for item in started["children"]]
    for n, child in enumerate(children):
        await _child_ends(child, answer=f"Result {n}.")
    with suppress_agent_run_enqueue():
        delivered = await SubagentResultDelivery(uow_factory()).deliver_owed(
            parent_id=parent.conversation_id
        )
    assert delivered == 3
    results = [
        message
        for message in await messages(parent.conversation_id)
        if message.role is MessageRole.USER
        and (message.metadata or {}).get("source") == RESULT_SOURCE
    ]
    (one,) = results
    assert all(f"Result {n}." in (one.text or "") for n in range(3))


async def test_e6_stop_stops_every_child(scenario) -> None:
    parent = await seeded_conversation(scenario)
    run_id = await running_run(parent)
    with suppress_agent_run_enqueue():
        started = await delegation.delegate(
            _as_parent(parent, run_id, "call-stop"),
            _request("A.", "B.", wait="none"),
        )
    children = [UUID(str(item["conversation_id"])) for item in started["children"]]

    async with create_uow_from_session_maker(async_session_maker) as uow:
        await stop_conversation(uow).stop(
            conversation_id=parent.conversation_id,
            user_id=parent.user_id,
            pod_id=parent.pod_id,
            agent_name=parent.agent_name,
        )
    for child in children:
        child_runs = await runs(child)
        assert {run.status for run in child_runs} <= {
            AgentRunStatus.STOPPED.value,
            AgentRunStatus.STOP_REQUESTED.value,
        }


async def test_e6_a_run_past_its_cap_is_told_so(scenario) -> None:
    parent = await seeded_conversation(scenario)
    run_id = await running_run(parent)
    ctx = _as_parent(parent, run_id, "call-many")
    with suppress_agent_run_enqueue():
        for batch in range(2):
            await delegation.delegate(
                ctx.model_copy(update={"tool_call_id": f"call-{batch}"}),
                _request(*[f"Task {batch}-{n}." for n in range(8)], wait="none"),
            )
        over = await delegation.delegate(
            ctx.model_copy(update={"tool_call_id": "call-over"}),
            _request("One too many.", wait="none"),
        )
    assert over["success"] is False
    assert str(MAX_CHILDREN_PER_RUN) in str(over["children"][0]["error"])
