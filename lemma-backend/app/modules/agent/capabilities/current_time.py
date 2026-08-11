"""Capability that makes the agent aware of the current time.

The note rides as a ``UserPromptPart`` inserted *immediately before* the
trailing user request, so the last thing the model reads is still the user's own
message. Three constraints shape that choice:

  * **Not a system part.** Anthropic hoists every ``SystemPromptPart`` out of the
    message stream into the top-level ``system`` parameter, ahead of the
    instruction blocks — so a trailing system note becomes the *first* system
    block, putting a value that changes every turn in front of the entire
    cacheable prefix. Position in the message list cannot fix that; only the part
    type can.
  * **Not after the user's turn.** A model answers the last user turn, so a note
    appended after it competes with the actual instruction. pydantic-ai merges
    consecutive ``ModelRequest``s with a stable sort, so ``[note, user]`` arrives
    as one request whose parts are in that order.
  * **Exactly once.** The graph writes the capability's result back into run
    state (``ctx.state.message_history[:] = messages``), so an unconditional
    append would accumulate one note per model step in a multi-step run.

The rendered text is cached for the run: rebuilding it per step would mutate a
message the provider has already seen and invalidate everything after it.
"""

from __future__ import annotations

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, ModelResponse, UserPromptPart

from app.modules.agent.infrastructure.pydantic_ai_compat import ModelRequestContext

from app.modules.agent.domain.runtime_notes import build_runtime_notes


class CurrentTimeCapability(AbstractCapability[object]):
    """Put the current UTC time just before the user's message on each request."""

    def __init__(self, *, id: str | None = "current_time") -> None:
        self._id = id
        self._notes: str | None = None
        self._marker: ModelRequest | None = None

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return self._id

    async def before_model_request(
        self,
        ctx: RunContext[object],
        request_context: ModelRequestContext,
    ) -> ModelRequestContext:
        messages = request_context.messages
        if self._already_present(messages):
            return request_context

        if self._notes is None:
            self._notes = build_runtime_notes()
        self._marker = ModelRequest(parts=[UserPromptPart(content=self._notes)])

        # Insert before the trailing user request so the user's text stays last.
        # Only ever at the tail: searching history for "the last user message"
        # would rewrite a *previous* turn on the resume path (where the run
        # continues with history only and no new user prompt), mutating the
        # cached prefix.
        if messages and _is_user_request(messages[-1]):
            request_context.messages = [*messages[:-1], self._marker, messages[-1]]
        else:
            request_context.messages = [*messages, self._marker]
        return request_context

    def _already_present(self, messages: list[object]) -> bool:
        """Has this run's note survived into the history the graph handed back?

        Identity first, since that is exact. The content fallback covers a
        history processor (summarization runs after this capability) rebuilding
        the tail into equivalent-but-not-identical messages.
        """
        if self._marker is not None and any(m is self._marker for m in messages):
            return True
        if self._notes is None:
            return False
        return any(
            isinstance(message, ModelRequest)
            and any(
                isinstance(part, UserPromptPart) and part.content == self._notes
                for part in message.parts
            )
            for message in messages
        )


def _is_user_request(message: object) -> bool:
    return (
        isinstance(message, ModelRequest)
        and not isinstance(message, ModelResponse)
        and any(isinstance(part, UserPromptPart) for part in message.parts)
    )
