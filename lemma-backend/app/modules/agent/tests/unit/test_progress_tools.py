"""The tool-schema facts a progress display reads, owned by the tools' module.

A surface draws progress; it does not know that the status line is called
``comment`` or that ``write_todos`` renders ``- [x] item`` lines.
"""

from __future__ import annotations

import json

from app.modules.agent.contracts.progress_tools import (
    TODO_TOOL_NAME,
    TodoStep,
    progress_comment_for_tool_call,
    todo_plan_from_tool_return,
)


def test_comment_is_read_from_the_preferred_key_first() -> None:
    assert (
        progress_comment_for_tool_call({"status": "s", "comment": "c", "progress": "p"})
        == "c"
    )
    assert progress_comment_for_tool_call({"progress_comment": "pc"}) == "pc"
    assert progress_comment_for_tool_call({"progress": "p"}) == "p"
    assert progress_comment_for_tool_call({"status": "s"}) == "s"


def test_comment_is_found_inside_a_request_wrapper() -> None:
    assert progress_comment_for_tool_call({"request": {"comment": "wrapped"}}) == (
        "wrapped"
    )


def test_blank_or_non_string_comments_are_no_comment() -> None:
    assert progress_comment_for_tool_call({"comment": "   "}) is None
    assert progress_comment_for_tool_call({"comment": 3}) is None
    assert progress_comment_for_tool_call({"request": "x"}) is None
    assert progress_comment_for_tool_call({}) is None
    assert progress_comment_for_tool_call(None) is None
    assert progress_comment_for_tool_call("comment") is None


def test_comment_is_returned_as_written() -> None:
    """Sanitising is the surface's business, not the schema reader's."""
    assert progress_comment_for_tool_call({"comment": " <think>x</think> hi "}) == (
        " <think>x</think> hi "
    )


def test_the_plan_is_the_whole_checklist_in_the_return() -> None:
    steps = todo_plan_from_tool_return(
        TODO_TOOL_NAME, {"todos": ["- [x] One", "- [ ] Two", "* [*] Three", "[X] Four"]}
    )
    assert steps == [
        TodoStep("One", True),
        TodoStep("Two", False),
        TodoStep("Three", True),
        TodoStep("Four", True),
    ]


def test_a_json_string_return_is_read_like_the_decoded_one() -> None:
    payload = {"todos": ["- [ ] Only step"]}
    assert todo_plan_from_tool_return(TODO_TOOL_NAME, json.dumps(payload)) == (
        todo_plan_from_tool_return(TODO_TOOL_NAME, payload)
    )


def test_other_tools_carry_no_plan() -> None:
    assert todo_plan_from_tool_return("run_query", {"todos": ["- [ ] x"]}) is None
    assert todo_plan_from_tool_return(None, {"todos": ["- [ ] x"]}) is None


def test_unreadable_or_empty_returns_carry_no_plan() -> None:
    assert todo_plan_from_tool_return(TODO_TOOL_NAME, "not json") is None
    assert todo_plan_from_tool_return(TODO_TOOL_NAME, ["- [ ] x"]) is None
    assert todo_plan_from_tool_return(TODO_TOOL_NAME, {"todos": "x"}) is None
    assert todo_plan_from_tool_return(TODO_TOOL_NAME, {"todos": []}) is None
    assert todo_plan_from_tool_return(TODO_TOOL_NAME, {"todos": [3, "- [ ] "]}) is None


def test_plain_lines_without_a_checkbox_are_skipped() -> None:
    steps = todo_plan_from_tool_return(
        TODO_TOOL_NAME, {"todos": ["just prose", "- [ ] Real"]}
    )
    assert steps == [TodoStep("Real", False)]


def test_the_tool_name_is_the_one_the_todo_capability_registers() -> None:
    """The constant lives apart from the tool, so tie them together."""
    import inspect

    from app.modules.agent.capabilities import todo

    assert f"def {TODO_TOOL_NAME}(" in inspect.getsource(todo.build_todo_toolset)
