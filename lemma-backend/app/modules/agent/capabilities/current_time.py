"""Capability that makes the agent aware of the current time.

The time rides as a fresh trailing ``SystemPromptPart`` on every model request
rather than baked into the system prompt. Two reasons it's a *system* note and
not a user turn:
  * the model answers the last *user* turn — a trailing user message gets
    mistaken for the actual instruction (the agent replies "noted" instead of
    doing the task), whereas a system note is read as ambient context;
  * it sits *after* the cached prefix and is never written back to stored
    history, so it advances each turn without breaking prompt caching.
"""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai._agent_graph import ModelRequestContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, SystemPromptPart

from app.modules.agent.domain.runtime_notes import build_runtime_notes


class CurrentTimeCapability(AbstractCapability[object]):
    """Append the current UTC time as a trailing system note on each request."""

    def __init__(self, *, id: str | None = "current_time") -> None:
        self._id = id

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return self._id

    async def before_model_request(
        self,
        ctx: RunContext[object],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        marker = ModelRequest(
            parts=[SystemPromptPart(content=build_runtime_notes())]
        )
        request_context.messages = [*request_context.messages, marker]
        return request_context
