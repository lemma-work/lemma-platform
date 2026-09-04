"""`ScheduleEventFilter`, answered by the system model.

Schedule's own port, schedule's own implementation. This was
`app/composition/schedule_filter.py`, and nothing about it varied by
deployment: it built the one filter this backend has, out of the system model,
and the two handlers that need a `ScheduleProcessor` imported the application
root to get one. Same move as `datastore_table_policy.py` next door, for the
same reason.

What kept it in the root was that it assembled a model itself -- resolve the
system runtime, then read a snapshot, credentials and a fallback model name off
the result and hand all three to agent's model factory. That is agent's job, and
`agent/contracts/model_runtime.py` now does it in one call, so the only thing
schedule states here is its own ceiling.
"""

from __future__ import annotations

import json

from pydantic_ai import Agent as PydanticAIAgent, UsageLimits
from pydantic_ai.output import StructuredDict

from app.modules.agent.contracts.model_runtime import resolve_system_runtime
from app.modules.pod.contracts.detached_reads import pod_organization_id_detached
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.infrastructure.adapters.schedule_event_publisher import (
    DurableScheduleEventPublisher,
)
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from app.modules.usage.contracts.execution import (
    UsageExecutionContext,
    record_pydantic_ai_result_usage,
    reserve_usage_for_runtime,
)

#: The one property this filter adds to whatever schema the schedule declared,
#: and the only one it reads back.
_SHOULD_PROCEED_PROPERTY: dict[str, object] = {
    "type": "boolean",
    "description": "Whether the workflow should proceed for this event",
}
DEFAULT_FILTER_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "should_proceed": _SHOULD_PROCEED_PROPERTY,
        "reason": {
            "type": "string",
            "description": "Brief explanation for the decision",
        },
    },
    "required": ["should_proceed"],
}
FILTER_USAGE_LIMITS = UsageLimits(
    request_limit=1,
    input_tokens_limit=32_000,
    output_tokens_limit=4_000,
    total_tokens_limit=36_000,
    count_tokens_before_request=True,
)

#: Characters of rendered event kept in the filter prompt.
#:
#: A budget, not a guess: it sits well inside ``input_tokens_limit`` above at
#: the conservative ~3 characters per token, leaving room for the system prompt
#: and the instruction. It exists because ``count_tokens_before_request`` is
#: silently dropped for models that cannot pre-count (see
#: ``resolve_system_runtime``, which returns the limits the model will actually
#: honour), so without a bound here the limit is only enforced *after* the
#: provider has been called and billed -- which is exactly what production was
#: doing, up to three times per event with retries.
_MAX_EVENT_CHARS = 60_000


class SystemModelScheduleFilter:
    """Evaluate a schedule's filter instruction against the system model."""

    async def filter_event(
        self,
        *,
        instruction: str,
        output_schema: dict[str, object] | None,
        event_payload: dict[str, object],
        schedule: ScheduleEntity,
    ) -> tuple[bool, dict[str, object] | None]:
        schema = self._prepare_schema(output_schema)
        runtime = await resolve_system_runtime(usage_limits=FILTER_USAGE_LIMITS)
        organization_id = (
            await pod_organization_id_detached(schedule.pod_id)
            if schedule.pod_id is not None
            else None
        )
        usage_context = UsageExecutionContext(
            user_id=schedule.user_id,
            organization_id=organization_id,
            pod_id=schedule.pod_id,
            agent_id=schedule.agent_id,
            source_type="schedule_filter",
            source_id=str(schedule.id) if schedule.id else None,
            workload_type="schedule",
            workload_id=schedule.id,
        )
        reservation = await reserve_usage_for_runtime(
            organization_id=usage_context.organization_id,
            user_id=usage_context.user_id,
            runtime_profile=runtime.runtime_profile,
        )

        agent = PydanticAIAgent(
            runtime.model,
            system_prompt=self._system_prompt(instruction),
            output_type=StructuredDict(schema),
        )
        result = None
        try:
            result = await agent.run(
                self._user_message(event_payload),
                usage_limits=runtime.usage_limits,
            )
        finally:
            await record_pydantic_ai_result_usage(
                ctx=usage_context,
                runtime_profile=runtime.runtime_profile,
                result=result,
                status="COMPLETED" if result is not None else "FAILED",
                reservation=reservation,
                metadata={"helper": "schedule_filter"},
            )

        output = result.output
        if not output.get("should_proceed", False):
            return False, None
        return True, output

    @staticmethod
    def _prepare_schema(output_schema: dict[str, object] | None) -> dict[str, object]:
        """The schedule's declared schema, with `should_proceed` guaranteed.

        The declared schema is a JSON document off a database row, so every
        branch it is read down is guarded rather than annotated: a `properties`
        that is not an object, or a `required` that is not a list, is replaced
        rather than merged into.
        """
        if not output_schema:
            return DEFAULT_FILTER_SCHEMA
        schema = dict(output_schema)
        declared_properties = schema.get("properties")
        properties: dict[str, object] = (
            dict(declared_properties) if isinstance(declared_properties, dict) else {}
        )
        properties.setdefault("should_proceed", _SHOULD_PROCEED_PROPERTY)
        declared_required = schema.get("required")
        required: list[object] = (
            list(declared_required) if isinstance(declared_required, list) else []
        )
        if "should_proceed" not in required:
            required.append("should_proceed")
        schema["properties"] = properties
        schema["required"] = required
        return schema

    @staticmethod
    def _system_prompt(instruction: str) -> str:
        return (
            "Analyze the incoming event for a workflow automation. Set "
            "should_proceed according to this filter instruction and return only "
            f"the requested structured output:\n\n{instruction}"
        )

    @staticmethod
    def _user_message(event_payload: dict[str, object]) -> str:
        """Render the event, bounded, because the filter's input is not ours.

        This embedded the whole trigger payload with ``indent=2``. A webhook
        body is whatever the provider chose to send, so the prompt had no upper
        size at all -- real payloads ran several times over the 32,000-token
        limit, and every one of those runs failed *after* the model had been
        called and billed, because the resolved system model cannot count
        tokens before a request.

        Truncating degrades the filter's judgement on a huge event. Failing
        outright removes it entirely, silently, on every fire. The first is the
        better trade, and the caller is told it happened.
        """
        rendered = json.dumps(event_payload, default=str, separators=(",", ":"))
        if len(rendered) > _MAX_EVENT_CHARS:
            kept = rendered[:_MAX_EVENT_CHARS]
            dropped = len(rendered) - _MAX_EVENT_CHARS
            rendered = (
                f"{kept}\n\n[event truncated: {dropped} of {len(rendered)} "
                f"characters omitted. Judge from what is shown.]"
            )
        return "Analyze this event:\n" + rendered


def create_schedule_processor() -> ScheduleProcessor:
    return ScheduleProcessor(
        filter_service=SystemModelScheduleFilter(),
        event_publisher=DurableScheduleEventPublisher(),
    )
