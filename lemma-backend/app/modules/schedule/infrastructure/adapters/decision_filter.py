"""`ScheduleEventFilter`, answered as a decision.

A schedule's filter is a closed question about the event that arrived: should
the work go ahead? It is asked through the decisions contract, so it runs on
whichever provider this deployment chose for decisions, is metered and
rate-limited like every other decision, and comes back as a typed answer rather
than a dict the model was merely asked to produce.

`filter_output_schema` may declare more questions to ask alongside it, and their
answers reach the target as `llm_output`. Only closed questions can be asked --
a choice, several choices, yes or no, a short scale. Anything else a stored
schema declares (the old default's free-text `reason`, open-ended numbers,
nested objects) is left out and logged, rather than failing every fire of a
schedule saved before the filter worked this way.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping

from app.core.log.log import get_logger
from app.modules.decisions.contracts import (
    DecisionCaller,
    DecisionMaker,
    DecisionRequest,
)
from app.modules.pod.contracts.detached_reads import pod_organization_id_detached
from app.modules.schedule.domain.interfaces import FilterVerdict
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.infrastructure.adapters.schedule_event_publisher import (
    DurableScheduleEventPublisher,
)
from app.modules.schedule.services.schedule_processor import ScheduleProcessor

logger = get_logger(__name__)

#: The one question every filter asks, and the only answer it routes on.
PROCEED = "should_proceed"
_PROCEED_QUESTION: dict[str, object] = {
    "type": "boolean",
    "description": "Should the scheduled work go ahead for this event?",
}
#: A decision asks at most this many questions; the filter's own comes first.
_MAX_QUESTIONS = 16
_KEY = re.compile(r"^[a-z][a-z0-9_]{0,63}$")

#: Bytes of rendered event sent as evidence. A decision refuses evidence over
#: 64 KiB rather than cutting it, so the cut is made here, where it can be said
#: out loud -- a webhook body is whatever the provider chose to send.
MAX_EVENT_BYTES = 60_000


class DecisionScheduleFilter:
    """Evaluate a schedule's filter instruction as a decision."""

    def __init__(self, *, decisions: DecisionMaker | None = None) -> None:
        self._decisions = decisions

    async def filter_event(
        self,
        *,
        instruction: str,
        output_schema: dict[str, object] | None,
        event_payload: dict[str, object],
        schedule: ScheduleEntity,
    ) -> FilterVerdict:
        schema, left_out = filter_questions(output_schema)
        if left_out:
            logger.info(
                "schedule.decision_filter.fields_not_asked.observed",
                schedule_id=str(schedule.id),
                fields=",".join(left_out),
            )
        organization_id = (
            await pod_organization_id_detached(schedule.pod_id)
            if schedule.pod_id is not None
            else None
        )
        result = await self._maker().decide(
            DecisionRequest(
                instruction=_instruction(instruction),
                evidence=render_event(event_payload),
                schema=schema,
                priority="background",
            ),
            DecisionCaller(
                user_id=schedule.user_id,
                organization_id=organization_id,
                pod_id=schedule.pod_id,
                agent_id=schedule.agent_id,
                workload_type="schedule",
                workload_id=schedule.id,
                source_type="schedule_filter",
                source_id=str(schedule.id) if schedule.id else None,
            ),
        )
        output: dict[str, object] = {
            key: list(answer.value) if isinstance(answer.value, tuple) else answer.value
            for key, answer in result.answers.items()
        }
        # Unsure is not yes: an event the filter could not judge is skipped,
        # and the skip is recorded with the answer that caused it.
        unsure = output.get(PROCEED) is None
        proceed = output.get(PROCEED) is True
        output[PROCEED] = proceed
        output["_decision"] = {
            "provider": result.provider,
            "model": result.model,
            "unsure": unsure,
            "confidence": {
                key: answer.confidence for key, answer in result.answers.items()
            },
        }
        return FilterVerdict(proceed=proceed, output=output)

    def _maker(self) -> DecisionMaker:
        if self._decisions is not None:
            return self._decisions
        from app.modules.decisions.contracts.decide import decision_maker

        return decision_maker()


def filter_questions(
    output_schema: Mapping[str, object] | None,
) -> tuple[dict[str, object], list[str]]:
    """The questions a filter asks, and the declared fields it cannot ask.

    `should_proceed` always, first. Each other declared property is asked when
    it is a closed question; one with no `description` is asked under its own
    name, because the schedules saved before this did not need one.
    """
    from app.modules.decisions.contracts.decide import decision_schema_problems

    properties: dict[str, object] = {PROCEED: _PROCEED_QUESTION}
    left_out: list[str] = []
    declared = (
        output_schema.get("properties") if isinstance(output_schema, Mapping) else None
    )
    for key, node in declared.items() if isinstance(declared, Mapping) else ():
        if key == PROCEED:
            continue
        question = _described(str(key), node)
        if (
            question is None
            or len(properties) >= _MAX_QUESTIONS
            or decision_schema_problems(
                {"type": "object", "properties": {key: question}}
            )
        ):
            left_out.append(str(key))
            continue
        properties[str(key)] = question
    return {"type": "object", "properties": properties}, left_out


def render_event(payload: Mapping[str, object]) -> str:
    """The event as compact JSON, cut to `MAX_EVENT_BYTES` with a note saying so.

    Cutting degrades the judgement of one huge event. Refusing it would remove
    the filter from every fire of a schedule whose provider sends large bodies.
    """
    rendered = json.dumps(payload, default=str, separators=(",", ":"))
    encoded = rendered.encode()
    if len(encoded) <= MAX_EVENT_BYTES:
        return rendered
    kept = encoded[:MAX_EVENT_BYTES].decode(errors="ignore")
    dropped = len(encoded) - MAX_EVENT_BYTES
    return (
        f"{kept}\n\n[event truncated: {dropped} of {len(encoded)} bytes "
        "omitted. Judge from what is shown.]"
    )


def _described(key: str, node: object) -> dict[str, object] | None:
    if not _KEY.match(key) or not isinstance(node, Mapping):
        return None
    question = {str(name): value for name, value in node.items()}
    description = question.get("description")
    if not isinstance(description, str) or not description.strip():
        question["description"] = key.replace("_", " ")
    return question


def _instruction(filter_instruction: str) -> str:
    return (
        "A schedule's work starts only for the events its filter lets through. "
        "Judge this event against the filter.\n\n"
        f"Filter: {filter_instruction}"
    )


def create_schedule_processor() -> ScheduleProcessor:
    return ScheduleProcessor(
        filter_service=DecisionScheduleFilter(),
        event_publisher=DurableScheduleEventPublisher(),
    )
