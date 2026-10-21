"""Delivers the run's queued notices, in the one position that is free.

Follows ``run_context.py`` on its first request, and for its reasons rather
than for symmetry:

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

A ``ProcessHistory`` capability, because the notice has to stay part of the
history it was added to: a later request that lost it would diverge from the
prefix the provider already read. Since pydantic-ai 2.55 a plain
``before_model_request`` hook edits only the request it is handed, and only
``ProcessHistory`` writes its result back into the run's history -- so a
notice delivered any other way held only while something registered after it
happened to write the whole list back.
"""

from __future__ import annotations

from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import (
    ModelMessage,
    ModelRequest,
    ModelResponse,
    UserPromptPart,
)

from app.modules.agent.domain.run_notices import RunNotices


class RunNoticeCapability(ProcessHistory[object]):
    """Put any notice this run has posted just before the user's message."""

    def __init__(self, notices: RunNotices, *, id: str | None = "run_notices") -> None:
        super().__init__(processor=self._deliver)
        self._notices = notices
        self._id = id

    def get_serialization_name(self) -> str | None:  # type: ignore[override]  # pragma: no cover - metadata
        return self._id

    async def _deliver(self, messages: list[ModelMessage]) -> list[ModelMessage]:
        waiting = self._notices.take()
        if not waiting:
            return messages
        notice = ModelRequest(parts=[UserPromptPart(content=text) for text in waiting])
        if messages and _is_user_request(messages[-1]):
            return [*messages[:-1], notice, messages[-1]]
        return [*messages, notice]


def _is_user_request(message: object) -> bool:
    return (
        isinstance(message, ModelRequest)
        and not isinstance(message, ModelResponse)
        and any(isinstance(part, UserPromptPart) for part in message.parts)
    )
