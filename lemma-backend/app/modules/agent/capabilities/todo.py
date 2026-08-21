"""Minimal todo / planning capability backed by conversation metadata.

A single tool — ``write_todos`` — takes plain markdown-checklist lines and merges
them into the stored list by their text:

  * ``"- [ ] Fetch the Q3 report"`` (or just ``"Fetch the Q3 report"``) adds/keeps
    an open task,
  * re-sending the same text with a checked box (``"- [x] Fetch the Q3 report"``,
    ``[X]`` / ``[*]`` also count) marks it done.

Lines are matched to existing tasks by their (trimmed, case-insensitive) text. A
single line updates one task without dropping the rest; multiple lines represent a
complete snapshot and replace the current list. This is deliberately simpler than a
structured-object ``TodoWrite``: small models reliably emit one string per line but
trip on nested objects. The tool ALWAYS returns the full list back as rendered
lines. Once every stored item is complete, the next unchecked item starts a fresh
plan rather than extending completed history.
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
            "Markdown checklist lines, e.g. '- [ ] Fetch the Q3 report'; '[x]' "
            "means done. Several lines replace the list; one line is matched by "
            "text and flips just that task."
        )
    )


# Accepts an optional leading list bullet ("-"/"*") then a checkbox: "[ ]" (open),
# or "[x]"/"[X]"/"[*]" (done). The remainder is the task text.
_CHECKBOX_RE = re.compile(r"^\s*(?:[-*]\s+)?\[(?P<mark>[ xX*])\]\s*(?P<text>.*)$")
_BULLET_RE = re.compile(r"^\s*[-*]\s+")
_DUPLICATE_CHECKBOX_PREFIX_RE = re.compile(r"^\s*(?:(?:[-*]\s*)?\[[ xX*]\]\s*){2,}")
_PLAN_XML_TAG_RE = re.compile(
    r"</?\s*(?:todos?|item)\b[^>]*>"
    r"|</\s*td\s*>(?=\s*(?:<\s*(?:item|/?todos?)\b|$))",
    flags=re.IGNORECASE,
)
_TEXT_STATUS_RE = re.compile(
    r"^(?P<text>.+?)\s+(?:(?P<separator>[-—:])\s*)?"
    r"(?P<status>done|complete|completed|in[\s_-]*progress|pending|todo)\s*$",
    flags=re.IGNORECASE,
)


def _split_todo_fragments(line: str) -> tuple[list[str], bool]:
    """Recover list items flattened into XML-like text by some tool parsers."""

    if not line or not line.strip():
        return [], False
    had_plan_tags = _PLAN_XML_TAG_RE.search(line) is not None
    if not had_plan_tags:
        return [line.strip()], False
    fragments = [
        fragment.strip()
        for fragment in _PLAN_XML_TAG_RE.sub("\n", line).splitlines()
        if fragment.strip()
    ]
    return fragments, True


def _remove_duplicate_checkbox_prefix(line: str) -> str:
    """Drop an erroneous outer checkbox while preserving the innermost state."""

    prefix = _DUPLICATE_CHECKBOX_PREFIX_RE.match(line)
    if prefix is None:
        return line
    marks = re.findall(r"\[([ xX*])\]", prefix.group(0))
    if not marks:
        return line
    return f"[{marks[-1]}] {line[prefix.end() :].lstrip()}"


def _text_status(text: str, *, infer_status: bool) -> tuple[str, bool | None]:
    """Read status prose emitted by malformed XML plans without guessing broadly."""

    match = _TEXT_STATUS_RE.match(text)
    if match is None:
        return text, None
    # Accept normal prose only when it came from a flattened plan. For untagged
    # calls, require an explicit separator ("— done") or an all-caps status label
    # such as "RESEARCH DONE", both shapes seen from XML-oriented tool parsers.
    if not infer_status and match.group("separator") is None and text != text.upper():
        return text, None
    status = match.group("status").lower().replace("_", " ").replace("-", " ")
    return match.group("text").strip(), status in {"done", "complete", "completed"}


def _parse_todo_line(
    line: str, *, infer_text_status: bool = False
) -> tuple[str, bool] | None:
    """Parse one input line into ``(content, done)``, or ``None`` if blank.

    Accepts a markdown checklist item ("- [ ] do x", "[x] done", "* [*] done") or
    plain text ("do x", treated as not-done). Blank/box-only lines are dropped so
    a stray empty line never creates a meaningless task.
    """
    if not line or not line.strip():
        return None
    line = _remove_duplicate_checkbox_prefix(line)
    match = _CHECKBOX_RE.match(line)
    if match:
        text = match.group("text").strip()
        if not text:
            return None
        text, text_done = _text_status(text, infer_status=infer_text_status)
        if not text:
            return None
        done = match.group("mark") in ("x", "X", "*")
        return text, text_done if text_done is not None else done
    text = _BULLET_RE.sub("", line).strip()
    if not text:
        return None
    text, text_done = _text_status(text, infer_status=infer_text_status)
    return (text, text_done if text_done is not None else False) if text else None


def _parse_todo_lines(lines: list[str]) -> list[tuple[str, bool]]:
    parsed: list[tuple[str, bool]] = []
    for line in lines:
        fragments, had_plan_tags = _split_todo_fragments(line)
        infer_text_status = had_plan_tags or len(fragments) > 1
        parsed.extend(
            item
            for item in (
                _parse_todo_line(
                    fragment,
                    infer_text_status=infer_text_status,
                )
                for fragment in fragments
            )
            if item is not None
        )
    return parsed


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
    index: dict[str, JsonObject] = {}
    for raw in stored:
        if not isinstance(raw, dict):
            continue
        content = str(raw.get("content") or "").strip()
        if not content:
            continue
        done = bool(raw.get("done")) or raw.get("status") == "completed"
        mark = "x" if done else " "
        recovered = _parse_todo_lines([f"- [{mark}] {content}"])
        if len(recovered) > 1:
            # A flattened multi-item row was an authoritative snapshot, not one
            # enormous task. Later rows can still incrementally update it.
            todos = []
            index = {}
        for recovered_content, recovered_done in recovered:
            key = _norm(recovered_content)
            existing = index.get(key)
            if existing is not None:
                existing["done"] = recovered_done
                continue
            entry: JsonObject = {
                "content": recovered_content,
                "done": recovered_done,
            }
            todos.append(entry)
            index[key] = entry
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

        A single line is upserted by its text; multiple lines replace the current
        list as one authoritative snapshot. Returns the full task list.
        """
        parsed = _parse_todo_lines(request.todos)
        todos = _normalize_stored(await store.read())

        # A finished task list is historical. The first unchecked item after all
        # stored items are complete starts a fresh plan instead of appending new
        # work to an ever-growing conversation-wide archive.
        if (
            todos
            and all(bool(item.get("done")) for item in todos)
            and any(not done for _, done in parsed)
        ):
            todos = []

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

        # More than one supplied item is the model's complete current plan.
        # Replacing on a full snapshot prevents wording changes and malformed
        # XML serializations from growing an unbounded conversation-wide list.
        if len(parsed) > 1:
            todos = []

        index = {_norm(t["content"]): t for t in todos}
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
