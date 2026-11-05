"""The schedule filter, asked as a decision.

What is the filter's own job, and so tested here: which questions it asks, how
it bounds an event it did not choose, and how an answer -- including "can't
tell" -- becomes a verdict. Asking is the decisions module's job and is faked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from uuid import uuid4

import pytest

from app.modules.decisions.contracts import (
    Answer,
    DecisionCaller,
    DecisionRequest,
    DecisionResult,
)
from app.modules.schedule.domain.schedule import ScheduleEntity, ScheduleType
from app.modules.schedule.infrastructure.adapters.decision_filter import (
    MAX_EVENT_BYTES,
    PROCEED,
    DecisionScheduleFilter,
    filter_questions,
    render_event,
)

pytestmark = pytest.mark.unit


@dataclass
class _Decisions:
    answers: dict[str, Answer]
    asked: list[tuple[DecisionRequest, DecisionCaller]] = field(default_factory=list)

    async def decide(
        self, request: DecisionRequest, caller: DecisionCaller
    ) -> DecisionResult:
        self.asked.append((request, caller))
        return DecisionResult(answers=self.answers, provider="typesafe", model="jev")


def _schedule() -> ScheduleEntity:
    # No pod, so the filter does not look up an organization in a database.
    return ScheduleEntity(
        id=uuid4(),
        user_id=uuid4(),
        pod_id=None,
        schedule_type=ScheduleType.WEBHOOK,
        config={"source": "custom"},
        filter_instruction="Only invoices over 1000",
    )


def test_should_proceed_is_always_asked_first() -> None:
    schema, left_out = filter_questions(None)

    assert list(schema["properties"]) == [PROCEED]  # type: ignore[call-overload]
    assert left_out == []


def test_closed_fields_are_asked_and_open_ones_are_left_out_by_name() -> None:
    declared = {
        "type": "object",
        "properties": {
            "should_proceed": {"type": "boolean"},
            "category": {"type": "string", "enum": ["invoice", "receipt"]},
            "priority": {"type": "integer", "minimum": 1, "maximum": 3},
            "reason": {"type": "string", "description": "Why"},
            "amount": {"type": "number"},
            "Bad Key": {"type": "boolean"},
        },
    }

    schema, left_out = filter_questions(declared)

    asked = schema["properties"]
    assert isinstance(asked, dict)
    assert list(asked) == [PROCEED, "category", "priority"]
    # A field saved without a description is asked under its own name.
    assert asked["category"]["description"] == "category"
    assert left_out == ["reason", "amount", "Bad Key"]


def test_a_huge_event_is_cut_to_the_bound_and_says_so() -> None:
    payload = {"rows": [{"i": index, "blob": "x" * 200} for index in range(2_000)]}

    rendered = render_event(payload)

    assert len(rendered.encode()) < MAX_EVENT_BYTES + 200
    assert "[event truncated:" in rendered


def test_a_small_event_is_sent_whole_as_compact_json() -> None:
    assert render_event({"a": 1, "b": [2]}) == json.dumps(
        {"a": 1, "b": [2]}, separators=(",", ":")
    )


async def test_yes_fires_and_carries_every_answer() -> None:
    decisions = _Decisions(
        {PROCEED: Answer(True, 0.9), "category": Answer("invoice", 0.8)}
    )
    schedule = _schedule()

    verdict = await DecisionScheduleFilter(decisions=decisions).filter_event(
        instruction="Only invoices over 1000",
        output_schema={
            "properties": {"category": {"type": "string", "enum": ["invoice", "x"]}}
        },
        event_payload={"total": 1200},
        schedule=schedule,
    )

    assert verdict.proceed is True
    assert verdict.output[PROCEED] is True
    assert verdict.output["category"] == "invoice"
    assert verdict.output["_decision"] == {
        "provider": "typesafe",
        "model": "jev",
        "unsure": False,
        "confidence": {PROCEED: 0.9, "category": 0.8},
    }
    request, caller = decisions.asked[0]
    assert "Only invoices over 1000" in request.instruction
    assert request.priority == "background"
    assert (caller.source_type, caller.workload_type, caller.workload_id) == (
        "schedule_filter",
        "schedule",
        schedule.id,
    )


async def test_cannot_tell_is_a_skip_and_says_it_was_unsure() -> None:
    decisions = _Decisions({PROCEED: Answer(None)})

    verdict = await DecisionScheduleFilter(decisions=decisions).filter_event(
        instruction="Only invoices",
        output_schema=None,
        event_payload={"text": "hello"},
        schedule=_schedule(),
    )

    assert verdict.proceed is False
    assert verdict.output[PROCEED] is False
    assert verdict.output["_decision"]["unsure"] is True  # type: ignore[index]
