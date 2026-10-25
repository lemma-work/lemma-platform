"""Run each tool call in a response once, whatever the provider repeated.

See `domain/tool_call_ids.py`. This half acts on what pydantic-ai executes:
after the response arrives and before its tools run.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelResponse, ToolCallPart
from pydantic_ai.models import ModelRequestContext

from app.core.log.log import get_logger
from app.modules.agent.domain.tool_call_ids import first_per_call_id

logger = get_logger(__name__)


class UniqueToolCallIdsCapability(AbstractCapability[Any]):
    async def after_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        kept, dropped = first_per_call_id(
            response.parts,
            lambda part: part.tool_call_id if isinstance(part, ToolCallPart) else None,
        )
        if not dropped:
            return response
        logger.warning(
            "agent.capabilities.duplicate_tool_call_ids.dropped.degraded",
            dropped=len(dropped),
            tool_names=sorted({part.tool_name for part in dropped}),
        )
        return replace(response, parts=kept)


__all__ = ["UniqueToolCallIdsCapability"]
