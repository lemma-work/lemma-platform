"""Composition adapter for schedule filters backed by a system model."""

from __future__ import annotations

import json
from typing import Any

from pydantic_ai import Agent as PydanticAIAgent, UsageLimits
from pydantic_ai.output import StructuredDict

from app.modules.agent.services.runtime_model_factory import (
    default_system_runtime,
    require_pydantic_ai_model_from_runtime_profile,
)
from app.modules.pod.infrastructure.pod_reads import resolve_pod_organization_id
from app.modules.schedule.domain.schedule import ScheduleEntity
from app.modules.schedule.infrastructure.adapters.schedule_event_publisher import (
    DurableScheduleEventPublisher,
)
from app.modules.schedule.services.schedule_processor import ScheduleProcessor
from app.modules.usage.services.pydantic_ai_tracking import (
    record_pydantic_ai_result_usage,
    reserve_usage_for_runtime,
)
from app.modules.usage.services.usage_context import UsageExecutionContext

DEFAULT_FILTER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "should_proceed": {
            "type": "boolean",
            "description": "Whether the workflow should proceed for this event",
        },
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


class SystemModelScheduleFilter:
    """Bind the schedule filter port to agent runtime and usage adapters."""

    async def filter_event(
        self,
        *,
        instruction: str,
        output_schema: dict[str, Any] | None,
        event_payload: dict[str, Any],
        schedule: ScheduleEntity,
    ) -> tuple[bool, dict[str, Any] | None]:
        schema = self._prepare_schema(output_schema)
        resolved_runtime = await default_system_runtime()
        runtime_profile = resolved_runtime.public_snapshot()
        model = require_pydantic_ai_model_from_runtime_profile(
            runtime_profile=runtime_profile,
            runtime_credentials=resolved_runtime.credentials or {},
            fallback_model_name=resolved_runtime.model_name_for_harness,
        )
        organization_id = (
            await resolve_pod_organization_id(schedule.pod_id)
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
            runtime_profile=runtime_profile,
        )

        agent = PydanticAIAgent(
            model,
            system_prompt=self._system_prompt(instruction),
            output_type=StructuredDict(schema),
        )
        result = None
        try:
            result = await agent.run(
                self._user_message(event_payload),
                usage_limits=FILTER_USAGE_LIMITS,
            )
        finally:
            await record_pydantic_ai_result_usage(
                ctx=usage_context,
                runtime_profile=runtime_profile,
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
    def _prepare_schema(output_schema: dict[str, Any] | None) -> dict[str, Any]:
        if not output_schema:
            return DEFAULT_FILTER_SCHEMA
        schema = dict(output_schema)
        properties = dict(schema.get("properties", {}))
        properties.setdefault(
            "should_proceed",
            {
                "type": "boolean",
                "description": "Whether the workflow should proceed for this event",
            },
        )
        required = list(schema.get("required", []))
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
    def _user_message(event_payload: dict[str, Any]) -> str:
        return "Analyze this event:\n" + json.dumps(
            event_payload, indent=2, default=str
        )


def create_schedule_processor() -> ScheduleProcessor:
    return ScheduleProcessor(
        filter_service=SystemModelScheduleFilter(),
        event_publisher=DurableScheduleEventPublisher(),
    )
