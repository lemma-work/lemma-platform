"""Delivers the run's queued notices, in the one position that is free.

Follows ``current_time.py`` exactly, and for its reasons rather than for
symmetry:

  * **A ``UserPromptPart``, never a ``SystemPromptPart``.** Anthropic hoists
    system parts out of the message stream into the top-level ``system``
    parameter, ahead of the instruction blocks -- so a note added that way
    lands in *front* of the whole cacheable prefix and changes it.
  * **Before the trailing user turn, not after it.** A model answers the last
    user message, and a notice placed after it competes with the actual
    instruction. Consecutive ``ModelRequest``s are merged with a stable sort,
    so ``[notice, user]`` arrives as one request in that order.
  * **Its own message, not appended to an existing one.** Editing the last
    request rewrites a message the provider has already been sent, and every
    token from there on has to be read again. A new message at the tail leaves
    everything before it untouched.

Delivered through ``before_model_request`` rather than a history processor
because the graph writes the result back into run state, so the notice stays
part of the history it was added to. A processor's copy is discarded after the
request, and the next turn's prefix would diverge at whatever it had edited.
"""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart

from app.modules.agent.domain.run_notices import RunNotices
from app.modules.agent.infrastructure.pydantic_ai_compat import ModelRequestContext


class RunNoticeCapability(AbstractCapability[object]):
    """Put any notice this run has posted just before the user's message."""

    def __init__(self, notices: RunNotices, *, id: str | None = "run_notices") -> None:
        self._notices = notices
        self._id = id

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return self._id

    async def before_model_request(
        self,
        ctx: RunContext[object],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        waiting = self._notices.take()
        if not waiting:
            return request_context
        notice = ModelRequest(parts=[UserPromptPart(content=text) for text in waiting])
        messages = request_context.messages
        if messages and _is_user_request(messages[-1]):
            request_context.messages = [*messages[:-1], notice, messages[-1]]
        else:
            request_context.messages = [*messages, notice]
        return request_context


def _is_user_request(message: object) -> bool:
    return (
        isinstance(message, ModelRequest)
        and not isinstance(message, ModelResponse)
        and any(isinstance(part, UserPromptPart) for part in message.parts)
    )
