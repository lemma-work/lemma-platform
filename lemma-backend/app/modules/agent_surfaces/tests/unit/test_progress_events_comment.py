"""A tool call's status line reaches a surface's progress display via the agent's contract.

Which argument carries the status line is the tools' schema, owned by the agent
(`agent/tests/unit/test_progress_tools.py`); what is pinned here is that the
surface reads it through that contract and still sanitises what it shows.
"""

from __future__ import annotations

from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
)
from app.modules.agent_surfaces.services.progress_events import (
    _progress_text_from_event,
)


def _call(args: object, tool_name: str = "web_search") -> AgentEvent:
    return AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_call(
            tool_name=tool_name, tool_call_id="c-1", tool_args=args
        ),
    )


def test_the_comment_in_the_call_is_the_progress_text() -> None:
    assert (
        _progress_text_from_event(_call({"request": {"comment": "Reading the docs"}}))
        == "Reading the docs"
    )
    assert _progress_text_from_event(_call({"progress": "Almost there"})) == (
        "Almost there"
    )


def test_a_call_without_one_falls_back_to_the_tool_name() -> None:
    assert _progress_text_from_event(_call({})) == "Using web_search"


def test_the_comment_is_sanitised_and_bounded_here_not_by_the_agent() -> None:
    assert _progress_text_from_event(_call({"comment": "  a   b  "})) == "a b"
    long = _progress_text_from_event(_call({"comment": "x" * 500}))
    assert long is not None and len(long) < 130 and long.endswith("...")


def test_a_comment_that_is_all_reasoning_yields_no_update() -> None:
    assert (
        _progress_text_from_event(_call({"comment": "<think>secret</think>"})) is None
    )
