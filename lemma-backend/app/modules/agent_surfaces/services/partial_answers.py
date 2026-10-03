"""Collecting the answers to a multi-question card one tap at a time.

``ask_user`` may ask several questions at once. A form-based platform (Slack,
Teams) submits them together, but WhatsApp has no form: each question is its
own interactive message and each tap is its own webhook carrying one answer.
Resolving the pause on the first tap answered one question, recorded the rest
as missing, and resumed the run -- and the person's taps on the remaining
questions then landed on a call that was already closed.

So a tap that leaves questions unanswered is held, and the pause is resolved
only once every header has an answer. Held in the conversation's own metadata,
merged in one statement, because two taps on two questions are two webhooks
that may be processed at the same moment.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from uuid import UUID

from pydantic import ValidationError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.contracts import AskUserRequest
from app.modules.agent.contracts import (
    conversations_for_surfaces as agent_conversations,
)
from app.modules.agent.contracts.interaction_replies import ask_user_request_dict

#: The metadata key the held answers live under, as ``{tool_call_id|header}``.
_KEY = "surface_partial_answers"
_SEP = "|"


@dataclass(frozen=True, slots=True)
class CollectedAnswers:
    """Every answer held for one pause so far, and how many are still owed."""

    answers: dict[str, object] = field(default_factory=dict)
    remaining: int = 0

    @property
    def complete(self) -> bool:
        return self.remaining == 0


async def _headers_of(
    uow: SqlAlchemyUnitOfWork, conversation_id: UUID, tool_call_id: str
) -> list[str]:
    """The headers the pause on ``tool_call_id`` asks, or none when unknowable."""
    pending = await agent_conversations.pending_question(uow, conversation_id)
    if pending is None or pending.tool_call_id != tool_call_id:
        return []
    raw = ask_user_request_dict(pending.tool_args)
    if raw is None:
        return []
    try:
        request = AskUserRequest.model_validate(raw)
    except ValidationError:
        return []
    return [question.header for question in request.questions]


async def collect_answers(
    uow: SqlAlchemyUnitOfWork,
    *,
    conversation_id: UUID,
    tool_call_id: str,
    submitted: dict[str, object],
) -> CollectedAnswers:
    """Add ``submitted`` to what is held, and say whether that completes it.

    A submission that answers everything on its own -- every form platform,
    and every single-question card -- is complete without touching the
    metadata. Where the questions cannot be read, the submission is treated as
    complete: holding answers for a pause nothing can describe would hold them
    forever.
    """
    headers = await _headers_of(uow, conversation_id, tool_call_id)
    if not headers or set(headers) <= set(submitted):
        return CollectedAnswers(answers=dict(submitted))
    held = await agent_conversations.merge_conversation_metadata_mapping(
        uow,
        conversation_id,
        _KEY,
        {f"{tool_call_id}{_SEP}{header}": value for header, value in submitted.items()},
    )
    prefix = f"{tool_call_id}{_SEP}"
    answers = {
        name.removeprefix(prefix): value
        for name, value in held.items()
        if name.startswith(prefix)
    }
    remaining = len([header for header in headers if header not in answers])
    return CollectedAnswers(answers=answers, remaining=remaining)


async def forget_answers(uow: SqlAlchemyUnitOfWork, *, conversation_id: UUID) -> None:
    """Drop the held answers once the pause they were for has been resolved.

    The whole key: a conversation is paused on one question at a time in every
    case a surface renders, so anything else under it is already orphaned.
    """
    await agent_conversations.set_conversation_metadata_value(
        uow, conversation_id, _KEY, None
    )


def progress_text(remaining: int) -> str:
    """What a tap that did not finish the card is answered with."""
    if remaining == 1:
        return "Got it. One more question to answer."
    return f"Got it. {remaining} more questions to answer."


__all__ = ["CollectedAnswers", "collect_answers", "forget_answers", "progress_text"]
