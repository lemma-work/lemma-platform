"""What the agent's tools say about progress, for a surface that draws it.

The tool schemas -- which argument carries a status line, how ``write_todos``
renders its checklist -- are the agent's. A surface asks here and draws what it
gets, so a change to either schema is a change in one module.

A submodule rather than `contracts/__init__`, like its siblings.
"""

from __future__ import annotations

from app.modules.agent.domain.progress_tools import (
    TODO_TOOL_NAME,
    TodoStep,
    progress_comment_for_tool_call,
    todo_plan_from_tool_return,
)

__all__ = [
    "TODO_TOOL_NAME",
    "TodoStep",
    "progress_comment_for_tool_call",
    "todo_plan_from_tool_return",
]
