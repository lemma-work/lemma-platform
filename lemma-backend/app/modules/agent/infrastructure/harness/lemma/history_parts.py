"""The two parts a run adds to its history, each kept where the cache allows.

``RunContextCapability`` puts the run's context part (``domain/prompt/
run_context``) next to the current turn on every request; ``RunNoticeCapability``
delivers queued notices just before the person's message. Both are
``ProcessHistory`` capabilities: since pydantic-ai 2.55 only ``ProcessHistory``
writes a hook's result back into the run's history, so a part placed any other
way held only while something registered after it rewrote the whole list.

Both are ``UserPromptPart``s, never ``SystemPromptPart``s: Anthropic hoists
system parts into the top-level ``system`` parameter, ahead of the instruction
blocks, which would put per-run text in front of the whole cacheable prefix.

Where the context part sits decides what the provider can reuse. On the first
request it goes just before the person's message (a model answers the last user
turn, and pydantic-ai merges consecutive requests with a stable sort). On every
later request it moves to the tail, so everything before it is byte-identical
to what the next run replays from storage without it. On Anthropic it opens
with a ``CachePoint``, marking a breakpoint at the end of the stable prefix.

A notice goes before the trailing user turn too, as its own message: editing
the last request would rewrite a message the provider has already been sent.
"""

from __future__ import annotations

from pydantic_ai.capabilities import ProcessHistory
from pydantic_ai.messages import (
    CachePoint,
    ModelMessage,
    ModelRequest,
    ModelResponse,
    TextPart,
    ToolCallPart,
    UserContent,
    UserPromptPart,
)

from app.modules.agent.domain.run_notices import RunNotices


class RunContextCapability(ProcessHistory[object]):
    """Keep the run's context part next to the newest turn, and in one place."""

    def __init__(
        self,
        text: str,
        *,
        mark_cache_boundary: bool = False,
        id: str | None = "run_context",
    ) -> None:
        super().__init__(processor=self._place)
        self._text = text
        self._mark_cache_boundary = mark_cache_boundary
        self._id = id
        self._placed = False

    def get_serialization_name(self) -> str | None:  # type: ignore[override]  # pragma: no cover - metadata
        return self._id

    async def _place(self, history: list[ModelMessage]) -> list[ModelMessage]:
        messages = self._without_part(history)
        before_user_turn = (
            not self._placed and bool(messages) and _is_user_request(messages[-1])
        )
        head = messages[:-1] if before_user_turn else messages
        part = UserPromptPart(content=self._content(head))
        tail = [messages[-1]] if before_user_turn else []
        self._placed = True
        return [*head, ModelRequest(parts=[part]), *tail]

    def _content(self, preceding: list[ModelMessage]) -> str | list[UserContent]:
        # A CachePoint attaches to the block before it, so it needs one: never
        # on the first request of a conversation, and only after a block
        # Anthropic accepts a breakpoint on.
        if self._mark_cache_boundary and _ends_in_cacheable_block(preceding):
            return [CachePoint(), self._text]
        return self._text

    def _without_part(self, messages: list[ModelMessage]) -> list[ModelMessage]:
        """The history with this run's part taken out of wherever it was.

        Matched by content, not identity: the graph merges consecutive requests
        into new ``ModelRequest`` objects, and history processors may rebuild
        the tail, so the part's container is not the object this placed.
        """
        kept: list[ModelMessage] = []
        for message in messages:
            if isinstance(message, ModelRequest) and any(
                self._is_part(part) for part in message.parts
            ):
                parts = [part for part in message.parts if not self._is_part(part)]
                if not parts:
                    continue
                message = ModelRequest(
                    parts=parts,
                    instructions=message.instructions,
                    timestamp=message.timestamp,
                )
            kept.append(message)
        return kept

    def _is_part(self, part: object) -> bool:
        if not isinstance(part, UserPromptPart):
            return False
        content = part.content
        if isinstance(content, str):
            return content == self._text
        return self._text in content


def _is_user_request(message: object) -> bool:
    return (
        isinstance(message, ModelRequest)
        and not isinstance(message, ModelResponse)
        and any(isinstance(part, UserPromptPart) for part in message.parts)
    )


def _ends_in_cacheable_block(messages: list[ModelMessage]) -> bool:
    """Whether the message before the part ends in a block that takes a breakpoint.

    A text or tool-call block from the model, or a request carrying user text or
    a tool result. A response ending in a thinking block does not: Anthropic
    refuses ``cache_control`` there, and a refused breakpoint fails the request.
    """
    if not messages:
        return False
    last = messages[-1]
    if isinstance(last, ModelResponse):
        return bool(last.parts) and isinstance(last.parts[-1], TextPart | ToolCallPart)
    return bool(last.parts)


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
