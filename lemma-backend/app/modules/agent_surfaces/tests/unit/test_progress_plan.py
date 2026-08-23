"""Reading the agent's checklist back off a ``write_todos`` return."""

from __future__ import annotations

import json

from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
)
from app.modules.agent_surfaces.services.progress_plan import (
    plan_from_event,
    render_plan,
)


def _return(result: object, *, tool_name: str = "write_todos") -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_return(
            tool_name=tool_name,
            tool_call_id="todo-1",
            tool_result=result,
        ),
    )


def test_the_return_is_read_not_the_arguments():
    """A check-off call carries one line; only the return carries the plan.

    ``write_todos`` merges a single line into the stored list before answering,
    so rendering the arguments would show a one-item plan every time an item was
    ticked off.
    """
    call = AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_call(
            tool_name="write_todos",
            tool_call_id="todo-1",
            tool_args={"request": {"todos": ["- [x] Step two"]}},
        ),
    )
    assert plan_from_event(call) is None

    plan = plan_from_event(_return({"todos": ["- [x] Step one", "- [ ] Step two"]}))
    assert plan is not None
    assert plan.total == 2
    assert plan.done_count == 1


def test_a_json_encoded_return_is_understood():
    # A remote harness relaying over MCP can deliver the same payload as a string.
    plan = plan_from_event(_return(json.dumps({"todos": ["- [ ] Only step"]})))
    assert plan is not None
    assert plan.total == 1


def test_another_tool_is_not_a_plan():
    assert (
        plan_from_event(_return({"todos": ["- [ ] x"]}, tool_name="run_query")) is None
    )


def test_an_empty_list_produces_no_update():
    # A cleared plan is a real state, but a blank update is worse than none.
    assert plan_from_event(_return({"todos": []})) is None


def test_the_next_step_is_marked_apart_from_the_ones_after_it():
    plan = plan_from_event(
        _return({"todos": ["- [x] Done", "- [ ] Now", "- [ ] Later"]})
    )
    assert plan is not None
    body = render_plan(plan)
    assert body.splitlines() == [
        "Working on it — 1 of 3 steps done.",
        "✅ Done",
        "⏳ Now",
        "⬜ Later",
    ]


def test_a_long_plan_collapses_the_history_it_already_showed():
    todos = [f"- [x] Step {n}" for n in range(1, 11)] + ["- [ ] Step 11"]
    plan = plan_from_event(_return({"todos": todos}))
    assert plan is not None
    lines = render_plan(plan).splitlines()

    assert lines[0] == "Working on it — 10 of 11 steps done."
    assert lines[1] == "✅ 3 earlier steps"
    assert len(lines) == 10
    assert lines[-1] == "⏳ Step 11"


def test_the_rendering_carries_no_markdown():
    # This string is delivered by every platform's progress path; a stray marker
    # behaves differently on each of them.
    plan = plan_from_event(_return({"todos": ["- [ ] Ship **it**"]}))
    assert plan is not None
    assert render_plan(plan) == "Working on it — 0 of 1 steps done.\n⏳ Ship **it**"
