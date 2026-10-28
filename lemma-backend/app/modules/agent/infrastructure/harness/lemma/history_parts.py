"""The two parts a run adds to its history, each kept where the cache allows.

``RunContextCapability`` writes the run's context part (``domain/prompt/
run_context``) into the history once, at the turn's position, and never moves
it; ``RunNoticeCapability`` delivers queued notices just before the person's
message. Both are ``ProcessHistory`` capabilities: since pydantic-ai 2.55 only
``ProcessHistory`` writes a hook's result back into the run's history.

Both are ``UserPromptPart``s, never ``SystemPromptPart``s: Anthropic hoists
system parts into the top-level ``system`` parameter, ahead of the instruction
blocks, which would put per-run text in front of the whole cacheable prefix.

Where the context part sits decides what the provider can reuse. It goes just
before the person's message on the run's first request (a model answers the
last user turn), or at the tail when the run has no new message (a resume), and
stays there: every later request of the run, and every later run replaying the
log, starts with the same bytes. It is history like any other part -- the log
carries it -- so a later run reads the context its turn was answered under, and
its own context after its own turn. It holds the run's start time and is stable
for the run, so nothing in it has to be fresh per request.

A notice goes before the person's words too. Both go into the trailing
request, which nothing has sent yet, rather than beside it: pydantic-ai merges
consecutive requests into one.
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


class RunContextCapability(ProcessHistory[object]):
    """Write the run's context part next to the run's turn, once, and leave it."""

    def __init__(self, text: str, *, id: str | None = "run_context") -> None:
        super().__init__(processor=self._place)
        self._text = text
        self._id = id
        self._placed = False

    def get_serialization_name(self) -> str | None:  # type: ignore[override]  # pragma: no cover - metadata
        return self._id

    async def _place(self, history: list[ModelMessage]) -> list[ModelMessage]:
        if self._placed and self._holds_part(history):
            return history
        # Placed once; again only when a rewrite (a compaction) dropped it, and
        # then at the tail, where it moves nothing already sent.
        before_turn = not self._placed
        self._placed = True
        return _folded(history, [UserPromptPart(content=self._text)], before_turn)

    def _holds_part(self, messages: list[ModelMessage]) -> bool:
        """Matched by content, not identity: the graph merges consecutive
        requests into new ``ModelRequest`` objects."""
        return any(
            isinstance(part, UserPromptPart) and part.content == self._text
            for message in messages
            if isinstance(message, ModelRequest)
            for part in message.parts
        )


def _folded(
    history: list[ModelMessage], parts: list[UserPromptPart], before_turn: bool
) -> list[ModelMessage]:
    """``history`` with ``parts`` in its trailing request.

    In it, never beside it: pydantic-ai merges consecutive requests into one,
    so a request of its own would be logged as sent and then replaced by the
    merged one -- a history that no longer starts with what was logged. Before
    the person's words when ``before_turn``, else after everything in it.
    """
    if not history or not isinstance(history[-1], ModelRequest):
        return [*history, ModelRequest(parts=list(parts))]
    last = history[-1]
    turn = next(
        (
            index
            for index, part in enumerate(last.parts)
            if isinstance(part, UserPromptPart)
        ),
        None,
    )
    at = turn if before_turn and turn is not None else len(last.parts)
    # In place, as pydantic-ai records its own deltas: the request is the one
    # about to be sent, and the run log labels what a run took in by identity.
    last.parts = [*last.parts[:at], *parts, *last.parts[at:]]
    return history


def _is_user_request(message: object) -> bool:
    return (
        isinstance(message, ModelRequest)
        and not isinstance(message, ModelResponse)
        and any(isinstance(part, UserPromptPart) for part in message.parts)
    )


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
        notices = [UserPromptPart(content=text) for text in waiting]
        return _folded(messages, notices, before_turn=True)
