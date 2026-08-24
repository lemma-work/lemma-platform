"""The agent's plan, read off a ``write_todos`` call and drawn for a surface.

The plan already exists — ``write_todos`` keeps a checklist in conversation
metadata and the agent ticks items off as it goes — but on a surface it was
invisible. Every tool call reduces to one status string, so a plan of five steps
reached the person as ``Using write_todos``: the single most informative thing
the agent produces, rendered as the least informative line it could be.

That matters most exactly where it was worst. On a long WhatsApp or Telegram run
the person has no other window into what is happening, so "which of the five
steps are done" is the whole answer to "is this still working". Here the
checklist is parsed back out of the tool's own return value and drawn as a
checklist.

The drawing is deliberately emoji and plain text — no Markdown. It cannot
unbalance a delimiter or need per-platform escaping on the way out.

It is drawn two ways, because a surface either has room for a checklist or it
does not. WhatsApp and Teams get the checklist. Telegram's live update is a
``tg-thinking`` chip whose HTML collapses newlines, so the checklist arrived
there as one run-on sentence with the marks stranded between the words — five
lines of structure flattened into something that read like a fault. What fits a
chip is one line, and :func:`render_plan_line` draws it: how far along the run
is, and the step it is on.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from app.modules.agent.contracts import (
    AgentEvent,
    AgentEventType,
    MessageDraft,
    MessageKind,
)

#: The planning tool from ``app.modules.agent.capabilities.todo``.
TODO_TOOL_NAME = "write_todos"

# ``write_todos`` returns its list already rendered as markdown checklist lines
# ("- [x] Fetch the Q3 report"), which is what we parse back.
_RENDERED_TODO_RE = re.compile(r"^\s*(?:[-*]\s+)?\[(?P<mark>[ xX*])\]\s*(?P<text>.+)$")

_DONE_MARK = "✅"
_ACTIVE_MARK = "⏳"
_PENDING_MARK = "⬜"

# A plan is a progress update, not the answer. Long ones are summarised rather
# than pasted whole so the update stays glanceable on a phone.
_MAX_RENDERED_ITEMS = 8
_MAX_ITEM_CHARS = 80


@dataclass(frozen=True)
class PlanItem:
    text: str
    done: bool


@dataclass(frozen=True)
class SurfacePlan:
    """A snapshot of the agent's checklist at one moment in a run."""

    items: tuple[PlanItem, ...]

    @property
    def total(self) -> int:
        return len(self.items)

    @property
    def done_count(self) -> int:
        return sum(1 for item in self.items if item.done)

    @property
    def signature(self) -> tuple[tuple[str, bool], ...]:
        """What must change before the plan is worth sending again.

        Compared rather than the rendered text so a re-send is driven by the
        plan actually moving, not by wording or by the same snapshot being
        written twice.
        """
        return tuple((item.text, item.done) for item in self.items)


def plan_from_event(event: AgentEvent) -> SurfacePlan | None:
    """Read the current plan out of a ``write_todos`` tool return.

    The *return* is used rather than the call arguments because the tool merges
    a single check-off line into the stored list before answering: the arguments
    can be one line ("- [x] step three"), while the return is always the whole
    list. Rendering the arguments would show a one-item plan every time an item
    was ticked off.
    """
    if event.type != AgentEventType.MESSAGE:
        return None
    data = event.data
    if not isinstance(data, MessageDraft):
        return None
    if data.kind is not MessageKind.TOOL_RETURN or data.tool_name != TODO_TOOL_NAME:
        return None
    lines = _rendered_lines(data.tool_result)
    if lines is None:
        return None
    items = tuple(
        PlanItem(text=parsed[0], done=parsed[1])
        for parsed in (_parse_rendered_line(line) for line in lines)
        if parsed is not None
    )
    # An empty list is a real state ("the plan was cleared"), but there is
    # nothing to show for it, and sending a blank update is worse than sending
    # none.
    return SurfacePlan(items=items) if items else None


def render_plan(plan: SurfacePlan) -> str:
    """Draw the plan as a checklist, in plain text with emoji marks.

    No Markdown: this string is delivered by every platform's progress path, and
    an unbalanced ``*`` or an unescaped ``.`` behaves differently on each of
    them. Emoji marks read the same everywhere.
    """
    if not plan.items:
        return ""
    lines = [_headline(plan)]
    lines.extend(_body_lines(plan))
    return "\n".join(lines)


def render_plan_line(plan: SurfacePlan) -> str:
    """Draw the plan as one line, for a surface with room for exactly one.

    Telegram's live progress is a thinking chip: one line of dimmed text, whose
    HTML eats the newlines a checklist is made of. So the checklist is not
    shortened here, it is replaced — by the count, which says whether the run is
    moving, and the step it is on, which says what it is doing. The marks go
    with the lines they belonged to: with nothing above or below it, ``⏳`` has
    nothing left to distinguish.

    The step also displaces the tool. A chip that said ``Using execute_python``
    was reporting the same moment as "Render the video" in the language of the
    machine rather than the language of the ask.
    """
    if not plan.items:
        return ""
    if plan.done_count >= plan.total:
        return _headline(plan)
    active = next(item for item in plan.items if not item.done)
    return f"{_progress_count(plan)} · {_clip(active.text)}"


def _headline(plan: SurfacePlan) -> str:
    if plan.done_count >= plan.total:
        return f"All {plan.total} steps done — writing up the answer now."
    return f"{_progress_count(plan)}."


def _progress_count(plan: SurfacePlan) -> str:
    return f"Working on it — {plan.done_count} of {plan.total} steps done"


def _body_lines(plan: SurfacePlan) -> list[str]:
    """The checklist, trimmed to what is worth reading on a phone.

    A long plan collapses its finished history to a count: the person already
    saw those tick off, and the useful part of a fifteen-step plan is where it
    is now and what is left.
    """
    active_seen = False
    rendered: list[str] = []
    for item in plan.items:
        if item.done:
            mark = _DONE_MARK
        elif not active_seen:
            mark = _ACTIVE_MARK
            active_seen = True
        else:
            mark = _PENDING_MARK
        rendered.append(f"{mark} {_clip(item.text)}")

    if len(rendered) <= _MAX_RENDERED_ITEMS:
        return rendered
    keep_from = len(rendered) - _MAX_RENDERED_ITEMS
    hidden = [item for item in plan.items[:keep_from]]
    summary = f"{_DONE_MARK} {len(hidden)} earlier steps"
    if any(not item.done for item in hidden):
        summary = f"…{len(hidden)} earlier steps"
    return [summary, *rendered[keep_from:]]


def _clip(text: str) -> str:
    collapsed = " ".join(text.split())
    if len(collapsed) <= _MAX_ITEM_CHARS:
        return collapsed
    return collapsed[: _MAX_ITEM_CHARS - 1].rstrip() + "…"


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


def _parse_rendered_line(line: str) -> tuple[str, bool] | None:
    match = _RENDERED_TODO_RE.match(line)
    if match is None:
        return None
    text = match.group("text").strip()
    if not text:
        return None
    return text, match.group("mark") in ("x", "X", "*")
