"""Run each tool call in a response once, whatever the provider repeated.

See `domain/tool_call_ids.py`. This half acts on what pydantic-ai executes:
after the response arrives and before its tools run.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelResponse, ModelResponsePart, ToolCallPart
from pydantic_ai.models import ModelRequestContext

from app.core.log.log import get_logger
from app.modules.agent.domain.tool_call_ids import (
    call_signature,
    unique_per_call_id,
    unparsed_signature,
)

logger = get_logger(__name__)


def _call_id(part: ModelResponsePart) -> str | None:
    return part.tool_call_id if isinstance(part, ToolCallPart) else None


def _signature(part: ModelResponsePart) -> str | None:
    assert isinstance(part, ToolCallPart)
    return call_signature(part.tool_name, part.args)


def _raw_signature(part: ModelResponsePart) -> str:
    assert isinstance(part, ToolCallPart)
    return unparsed_signature(part.tool_name, part.args)


def _renamed(part: ModelResponsePart, call_id: str) -> ModelResponsePart:
    assert isinstance(part, ToolCallPart)
    return replace(part, tool_call_id=call_id)


def unique_call_parts(
    parts: list[ModelResponsePart],
) -> tuple[list[ModelResponsePart], list[ModelResponsePart]]:
    """The response's parts with one call per id; and the repeats dropped."""
    return unique_per_call_id(
        parts,
        call_id=_call_id,
        signature=_signature,
        raw_signature=_raw_signature,
        renamed=_renamed,
    )


class UniqueToolCallIdsCapability(AbstractCapability[Any]):
    async def after_model_request(
        self,
        ctx: RunContext[Any],
        *,
        request_context: ModelRequestContext,
        response: ModelResponse,
    ) -> ModelResponse:
        kept, dropped = unique_call_parts(response.parts)
        sent_ids = {_call_id(part) for part in response.parts}
        renamed = [
            part
            for part in kept
            if isinstance(part, ToolCallPart) and part.tool_call_id not in sent_ids
        ]
        if not dropped and not renamed:
            return response
        logger.warning(
            "agent.capabilities.duplicate_tool_call_ids.dropped.degraded",
            dropped=len(dropped),
            renamed=len(renamed),
            tool_names=sorted(
                {
                    part.tool_name
                    for part in (*dropped, *renamed)
                    if isinstance(part, ToolCallPart)
                }
            ),
        )
        return replace(response, parts=kept)


__all__ = ["UniqueToolCallIdsCapability", "unique_call_parts"]
