"""The todo / planning tool, stored on the conversation's ``todos`` column.

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

import difflib
import re
from uuid import UUID

from pydantic import BaseModel, Field

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.domain.todos import Todo
from app.modules.agent.domain.tools import (
    ReplayClass,
    ToolGroup,
    ToolPolicy,
    ToolSpec,
    tool,
)
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.infrastructure.db.conversations import Conversations


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


def _close_open_match(content: str, todos: list[JsonObject]) -> JsonObject | None:
    """The open task a reworded check-off almost certainly meant.

    Exact text is how a single line finds its task, and a model that finishes
    "Fetch the Q3 report" and reports "- [x] Fetch Q3 report" used to *add* a
    second, completed item -- leaving the planned one open forever while the
    list quietly grew. Matched conservatively: only when one open task is close
    enough that nothing else competes, so "add a task I forgot" still adds.
    """
    open_by_key = {
        _norm(str(item["content"])): item for item in todos if not item.get("done")
    }
    if not open_by_key:
        return None
    matches = difflib.get_close_matches(
        _norm(content), list(open_by_key), n=2, cutoff=0.85
    )
    if len(matches) != 1:
        return None
    return open_by_key[matches[0]]


def _merge_todos(
    parsed: list[tuple[str, bool]], todos: list[JsonObject]
) -> list[JsonObject]:
    """Upsert parsed lines into the stored list, in place."""
    index = {_norm(str(item["content"])): item for item in todos}
    single_check_off = len(parsed) == 1 and parsed[0][1]
    for content, done in parsed:
        existing = index.get(_norm(content))
        if existing is None and single_check_off:
            existing = _close_open_match(content, todos)
        if existing is not None:
            existing["done"] = done
            continue
        entry: JsonObject = {"content": content, "done": done}
        todos.append(entry)
        index[_norm(content)] = entry
    return todos


def _todo_result(todos: list[JsonObject]) -> JsonObject:
    """The list, plus the one instruction that keeps it honest.

    The usage prompt says to check items off as they finish, and it is many tool
    calls away by the time one does. This rides back on every call, so the next
    step is stated where the model is actually looking.
    """
    result: JsonObject = {"success": True, "todos": [_render(item) for item in todos]}
    open_items = [item for item in todos if not item.get("done")]
    if not todos:
        return result
    if not open_items:
        result["reminder"] = (
            "Every item is checked off. Report what you produced rather than "
            "extending a finished plan."
        )
        return result
    next_item = str(open_items[0].get("content", ""))
    result["next"] = next_item
    # "As soon as it is finished", not "before starting the next": the plan is
    # a record of progress, not a lock on the work, and independent steps can
    # run in the same turn.
    result["reminder"] = (
        f"{len(todos) - len(open_items)} of {len(todos)} done. Next: {next_item}. "
        f'Check it off with write_todos(["- [x] {next_item}"]) as soon as it is '
        "finished, not in a batch at the end."
    )
    return result


class ConversationTodoStore:
    """Read and replace one conversation's todo list."""

    def __init__(
        self, *, uow_factory: UnitOfWorkFactory, conversation_id: UUID
    ) -> None:
        self._uow_factory = uow_factory
        self._conversation_id = conversation_id

    async def read(self) -> list[JsonObject]:
        async with self._uow_factory() as uow:
            todos = await Conversations(uow).get_todos(self._conversation_id)
        return [todo.to_json() for todo in todos]

    async def write(self, todos: list[JsonObject]) -> None:
        # The column is written on its own, so nothing else on the row moves.
        async with self._uow_factory() as uow:
            await Conversations(uow).set_todos(
                self._conversation_id,
                [todo for item in todos if (todo := Todo.from_json(item)) is not None],
            )
            await uow.commit()


def todo_specs(*, uow_factory: UnitOfWorkFactory) -> tuple[ToolSpec, ...]:
    """``write_todos``, writing to the calling run's conversation."""

    async def write_todos(ctx: ToolContext, request: WriteTodosRequest) -> JsonObject:
        """Add or update task-list items from markdown checklist lines.

        A single line is upserted by its text; multiple lines replace the current
        list as one authoritative snapshot. Returns the full task list.
        """
        store = ConversationTodoStore(
            uow_factory=uow_factory, conversation_id=ctx.conversation_id
        )
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
            result = _todo_result(todos)
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

        todos = _merge_todos(parsed, todos)
        await store.write(todos)
        return _todo_result(todos)

    # Replaced whole: running it again leaves the same list.
    return (tool(write_todos, ToolGroup.TODO, ToolPolicy(replay=ReplayClass.SAFE)),)
