"""What the agent's tools say about how a run is going.

Two facts about tool schemas that a progress display needs, kept with the tools
they describe rather than re-derived by whichever surface draws the display:

* a tool call may carry a short human-readable status line in its arguments;
* ``write_todos`` returns the agent's whole checklist as rendered markdown lines.

The surface decides how (and whether) to draw them. It does not get to know
that the status line is called ``comment`` or that a checklist item is
``- [x] text``.

Pure functions, no I/O.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

#: The planning tool from ``app.modules.agent.capabilities.todo``.
TODO_TOOL_NAME = "write_todos"

#: Argument names a tool call may put its human-readable status line under, in
#: the order they are preferred.
_COMMENT_KEYS = ("comment", "progress_comment", "progress", "status")

# ``write_todos`` returns its list already rendered as markdown checklist lines
# ("- [x] Fetch the Q3 report"), which is what we parse back.
_RENDERED_TODO_RE = re.compile(r"^\s*(?:[-*]\s+)?\[(?P<mark>[ xX*])\]\s*(?P<text>.+)$")


@dataclass(frozen=True, slots=True)
class TodoStep:
    """One line of the agent's checklist."""

    text: str
    done: bool


def progress_comment_for_tool_call(tool_args: object) -> str | None:
    """The status line a tool call put in its own arguments, if it did.

    Looked up in the call's arguments and, because a tool that takes one pydantic
    model persists its args either flattened or wrapped in ``{"request": {...}}``,
    inside that wrapper too. Returned as written: sanitising it for display is the
    surface's business.
    """
    if not isinstance(tool_args, dict):
        return None
    for key in _COMMENT_KEYS:
        raw = tool_args.get(key)
        if isinstance(raw, str) and raw.strip():
            return raw
    request = tool_args.get("request")
    if isinstance(request, dict):
        return progress_comment_for_tool_call(request)
    return None


def todo_plan_from_tool_return(
    tool_name: str | None, tool_result: object
) -> list[TodoStep] | None:
    """The checklist a ``write_todos`` return carries, or ``None`` if it carries none.

    The *return* is what carries the plan, not the call arguments: the tool
    merges a single check-off line into the stored list before answering, so the
    arguments can be one line ("- [x] step three") while the return is always the
    whole list. ``None`` covers "not that tool", "unreadable result" and "an empty
    list" alike; none of them has a plan to show.
    """
    if tool_name != TODO_TOOL_NAME:
        return None
    lines = _rendered_lines(tool_result)
    if lines is None:
        return None
    steps = [step for step in (_parse_rendered_line(line) for line in lines) if step]
    return steps or None


def _rendered_lines(tool_result: object) -> list[str] | None:
    """Pull the ``todos`` list out of a tool return, dict or JSON string.

    Harnesses differ on whether a tool return arrives decoded: the in-process
    one hands over the dict the tool built, while a remote harness relaying over
    MCP can deliver the same payload as a JSON string.
    """
    result = tool_result
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except ValueError:
            return None
    if not isinstance(result, dict):
        return None
    todos = result.get("todos")
    if not isinstance(todos, list):
        return None
    return [line for line in todos if isinstance(line, str)]


def _parse_rendered_line(line: str) -> TodoStep | None:
    match = _RENDERED_TODO_RE.match(line)
    if match is None:
        return None
    text = match.group("text").strip()
    if not text:
        return None
    return TodoStep(text=text, done=match.group("mark") in ("x", "X", "*"))
