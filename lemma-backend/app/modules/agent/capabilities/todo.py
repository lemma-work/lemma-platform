"""Minimal todo / planning capability backed by conversation metadata.

A single tool — ``write_todos`` — takes plain markdown-checklist lines and merges
them into the stored list by their text:

  * ``"- [ ] Fetch the Q3 report"`` (or just ``"Fetch the Q3 report"``) adds/keeps
    an open task,
  * re-sending the same text with a checked box (``"- [x] Fetch the Q3 report"``,
    ``[X]`` / ``[*]`` also count) marks it done.

Lines are matched to existing tasks by their (trimmed, case-insensitive) text, so
the model can send the WHOLE list or just a SINGLE line to flip one task — either
way nothing else is dropped. This is deliberately simpler than a structured-object,
full-replace ``TodoWrite``: small models reliably emit one string per line but trip
on nested objects or on resending a complete list every call. The tool ALWAYS
returns the full list back as rendered lines.
"""

from __future__ import annotations

import re
from uuid import UUID

from pydantic import BaseModel, Field
from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.toolsets import AbstractToolset, FunctionToolset

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.capabilities.todo_storage import ConversationTodoStore
from app.modules.agent.domain.prompts import load_todo_prompt
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.tools.context import BaseAgentContext


class WriteTodosRequest(BaseModel):
    todos: list[str] = Field(
        description=(
            "One markdown checklist line per task. Use '[ ]' for a task still to "
            "do and '[x]' once it's done (e.g. '- [ ] Fetch the Q3 report'); a "
            "line with no checkbox is treated as not-done. Send the whole list, "
            "or just the single line you want to flip — lines are matched to "
            "existing tasks by their text, so the rest of the list is preserved. "
            "The full, updated list is always returned."
        )
    )


# Accepts an optional leading list bullet ("-"/"*") then a checkbox: "[ ]" (open),
# or "[x]"/"[X]"/"[*]" (done). The remainder is the task text.
_CHECKBOX_RE = re.compile(r"^\s*(?:[-*]\s+)?\[(?P<mark>[ xX*])\]\s*(?P<text>.*)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+")


def _parse_todo_line(line: str) -> tuple[str, bool] | None:
    """Parse one input line into ``(content, done)``, or ``None`` if blank.

    Accepts a markdown checklist item ("- [ ] do x", "[x] done", "* [*] done") or
    plain text ("do x", treated as not-done). Blank/box-only lines are dropped so
    a stray empty line never creates a meaningless task.
    """
    if not line or not line.strip():
        return None
    match = _CHECKBOX_RE.match(line)
    if match:
        text = match.group("text").strip()
        if not text:
            return None
        return text, match.group("mark") in ("x", "X", "*")
    text = _BULLET_RE.sub("", line).strip()
    if not text:
        return None
    return text, False


def _norm(text: str) -> str:
    """Match key for upserts: trimmed + case-insensitive."""
    return text.strip().casefold()


def _render(item: JsonObject) -> str:
    mark = "x" if item.get("done") else " "
    return f"- [{mark}] {item.get('content', '')}"


def _normalize_stored(stored: list[JsonObject]) -> list[JsonObject]:
    """Coerce stored rows to ``{content, done}``, tolerating the legacy
    ``{content, status, active_form}`` shape from before the simplification."""
    todos: list[JsonObject] = []
    for raw in stored:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        done = bool(raw.get("done")) or raw.get("status") == "completed"
        todos.append({"content": content, "done": done})
    return todos


class TodoCapability(AbstractCapability[object]):
    """The todo tool plus its usage instructions."""

    def __init__(self, toolset: AbstractToolset[object]) -> None:
        self._toolset = toolset

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return "todo"

    def get_toolset(self) -> AbstractToolset[object]:
        return self._toolset

    def get_instructions(self) -> str:
        return load_todo_prompt()


# Stable id so the LEMMA assembler can spot the per-conversation todo toolset
# (built by RunToolAssembler) and wrap it with TodoCapability for its instructions.
TODO_TOOLSET_ID = "lemma_todo"


def build_todo_toolset(
    *,
    uow_factory: UnitOfWorkFactory,
    conversation_id: UUID,
) -> FunctionToolset[BaseAgentContext]:
    """Build the todo FunctionToolset persisting to conversation metadata.

    Shared by both harness families: RunToolAssembler includes it directly (so it
    reaches remote harnesses over MCP), and the LEMMA assembler wraps it in
    TodoCapability.
    """
    store = ConversationTodoStore(
        uow_factory=uow_factory, conversation_id=conversation_id
    )

    async def write_todos(
        ctx: RunContext[BaseAgentContext], request: WriteTodosRequest
    ) -> JsonObject:
        """Add or update task-list items from markdown checklist lines.

        Each line is upserted by its text: a new line is appended, a line whose
        text matches an existing task flips that task's done state. Send one line
        to flip one task, or the whole list at once. Returns the full task list.
        """
        parsed = [
            item
            for item in (_parse_todo_line(line) for line in request.todos)
            if item is not None
        ]
        todos = _normalize_stored(await store.read())
        index = {_norm(t["content"]): t for t in todos}

        if not parsed:
            # Nothing real to merge: return the current list rather than wiping it.
            result: JsonObject = {
                "success": True,
                "todos": [_render(t) for t in todos],
            }
            if not todos:
                result["note"] = (
                    "No tasks provided. Only use write_todos for real, multi-step "
                    "work; for trivial requests just answer directly."
                )
            return result

        for content, done in parsed:
            existing = index.get(_norm(content))
            if existing is not None:
                existing["done"] = done
            else:
                entry = {"content": content, "done": done}
                todos.append(entry)
                index[_norm(content)] = entry

        await store.write(todos)
        return {"success": True, "todos": [_render(t) for t in todos]}

    return FunctionToolset[BaseAgentContext](tools=[write_todos], id=TODO_TOOLSET_ID)


def build_todo_capability(
    *,
    uow_factory: UnitOfWorkFactory,
    conversation_id: UUID,
) -> TodoCapability:
    """Wrap the todo toolset in a capability (adds the task-list instructions)."""
    return TodoCapability(
        build_todo_toolset(uow_factory=uow_factory, conversation_id=conversation_id)
    )
