"""Puts the run's context part next to the current turn on every request.

The part is what changes between runs (``domain/run_context_prompt``): the
clock, the task list, memory, the attached doc, open requests. It is never
stored, so where it sits decides what the provider can reuse:

  * **First request: just before the person's message.** A model answers the
    last user turn, so context placed after it competes with the request, and
    pydantic-ai merges consecutive ``ModelRequest``s with a stable sort, so
    ``[context, user]`` arrives as one request in that order.
  * **Every later request: at the tail.** Left where it was, it would sit in the
    middle of this run's transcript -- and the next run, which replays that
    transcript from storage without it, would diverge there and re-read the
    whole run. Moved to the tail, everything before it is byte-identical to
    what the next run replays, and the cost is re-reading the part itself.
  * **A ``UserPromptPart``, never a ``SystemPromptPart``.** Anthropic hoists
    system parts into the top-level ``system`` parameter, ahead of the
    instruction blocks, which would put per-run text in front of the whole
    cacheable prefix.

On Anthropic the part opens with a ``CachePoint``, which marks a cache
breakpoint at the end of whatever precedes it -- the stable prefix. The
message-level breakpoint ``PromptCachingCapability`` asks for lands on the last
block, which on later requests is this part and so never matches again; this
one is what the next request, and the next run, read from cache.

A ``ProcessHistory`` capability, so where it puts the part is where the run's
history has it: since pydantic-ai 2.55 only ``ProcessHistory`` writes a hook's
result back into the run's history, and a plain ``before_model_request`` hook
held only while something registered after it wrote the whole list back.
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
