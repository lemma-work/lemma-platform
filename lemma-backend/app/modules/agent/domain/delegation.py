"""Delegating work to sub-agents: the limits, and the reports that come back.

An agent hands several tasks out in one call (``delegate``) and, waiting on
them all, wakes once with every child's report. These are the rules that call
and that wake share: how many children one run may have, how deep delegation
goes, and how the reports are shaped and cut so a large fan-out does not
flood the parent's history -- every later turn of the parent replays them.

Pure: no reads, no writes.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.modules.agent.domain.subagent_results import result_text
from app.modules.agent.domain.value_objects import JsonValue

#: Tasks one ``delegate`` call may hand out.
MAX_TASKS_PER_CALL = 8
#: Children one run may have spawned in all. A backstop for a run spawning in
#: a loop, not a schedule for one fanning out on purpose.
MAX_CHILDREN_PER_RUN = 16
#: A sub-agent delegates no further: it is shown the delegation tools, as its
#: parent is, and every call of them is refused (``domain/toolsets``), so a
#: fan-out is one level and its cost is visible.
MAX_DEPTH = 1

#: Characters of reports one wake carries in all. Split evenly between the
#: children; a report past its share is cut, with where to read the rest.
REPORTS_BUDGET_CHARS = 24_000
#: However many children there are, each keeps at least this much.
MIN_REPORT_CHARS = 1_500

#: How a child stands, as its report says.
FINISHED = "COMPLETED"
FAILED = "FAILED"
STOPPED = "STOPPED"
RUNNING = "RUNNING"
NEEDS_INPUT = "NEEDS_INPUT"
GONE = "GONE"


@dataclass(frozen=True, slots=True)
class ChildReport:
    """One child, as the parent hears of it."""

    conversation_id: str
    run_id: str
    agent: str
    status: str
    output: JsonValue = None
    error: str | None = None
    #: What a child that stopped to ask is asking, for the parent to answer.
    question: JsonValue = None

    @property
    def settled(self) -> bool:
        return self.status not in (RUNNING, NEEDS_INPUT)


def _cut(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return (
        text[:limit]
        + f"\n\n[Cut at {limit} of {len(text)} characters. The child wrote the "
        "whole report as its last message: read it with "
        "query_subagents(mode='messages').]"
    )


def report_payload(reports: list[ChildReport]) -> list[dict[str, object]]:
    """The reports as the parent's tool return carries them, cut to fit."""
    share = max(MIN_REPORT_CHARS, REPORTS_BUDGET_CHARS // max(1, len(reports)))
    payload: list[dict[str, object]] = []
    for report in reports:
        item: dict[str, object] = {
            "agent": report.agent,
            "conversation_id": report.conversation_id,
            "run_id": report.run_id,
            "status": report.status,
        }
        text = result_text(report.output)
        if text:
            item["report"] = _cut(text, share)
        if report.error:
            item["error"] = report.error
        if report.question is not None:
            item["question"] = report.question
        payload.append(item)
    return payload


__all__ = [
    "ChildReport",
    "FAILED",
    "FINISHED",
    "GONE",
    "MAX_CHILDREN_PER_RUN",
    "MAX_DEPTH",
    "MAX_TASKS_PER_CALL",
    "NEEDS_INPUT",
    "RUNNING",
    "STOPPED",
    "report_payload",
]
