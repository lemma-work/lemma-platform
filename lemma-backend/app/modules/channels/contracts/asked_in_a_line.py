"""Questions an agent put to one member in their line, across many pods.

``channel_post`` with ``wants_answer`` writes a line to a member's own channel
and a question on it that waits on them. Their inbox counts it, per pod; an
organization's overview wants every pod's at once, which this answers in one
statement on the waiting-member index.

Only questions a conversation asked by message (``asker = MESSAGE``). A paused
``ask_user`` or ``request_approval`` is posted to a line too, but the overview
already lists it as its conversation's pause, and a workflow's form as its
run's wait: listing their lines as well would show each one twice.

**The pods are the caller's to have authorized.** A question waits on exactly
one person, so every one listed for ``user_id`` is theirs to answer; the caller
passes only pods the member may open.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.entities import QuestionAsker
from app.modules.channels.infrastructure.question_models import ChannelQuestionModel
from app.modules.channels.infrastructure.questions import still_waiting


@dataclass(frozen=True, slots=True)
class AskedInALine:
    """A question waiting on the member in their line, and how to answer it."""

    pod_id: UUID
    channel_id: UUID
    message_id: UUID
    #: What was asked, in the agent's words.
    prompt: str | None
    #: The answers it offered, when it offered some; an answer in words is
    #: always allowed besides.
    choices: tuple[str, ...]
    since: datetime


def _prompt(question: dict[str, object]) -> str | None:
    said = question.get("prompt")
    return said.strip() or None if isinstance(said, str) else None


def offered_choices(question: dict[str, object]) -> tuple[str, ...]:
    """The labels of the first question's options, in the shape ``ask_user``
    keeps them and the chat draws as buttons."""
    asked = question.get("questions")
    if not isinstance(asked, list) or not asked or not isinstance(asked[0], dict):
        return ()
    options = asked[0].get("options")
    if not isinstance(options, list):
        return ()
    return tuple(
        label
        for option in options
        if isinstance(option, dict)
        and isinstance(label := option.get("label"), str)
        and label.strip()
    )


async def asked_in_a_line(
    uow: SqlAlchemyUnitOfWork,
    *,
    user_id: UUID,
    pod_ids: Sequence[UUID],
    limit: int,
) -> tuple[list[AskedInALine], bool]:
    """What agents asked this member in their lines in these pods, longest
    waiting first; whether more were waiting than ``limit`` comes beside it."""
    if not pod_ids:
        return [], False
    q = ChannelQuestionModel
    rows = (
        await uow.session.execute(
            select(q.pod_id, q.channel_id, q.message_id, q.question, q.created_at)
            .where(
                q.awaits_user_id == user_id,
                q.pod_id.in_(pod_ids),
                q.asker == QuestionAsker.MESSAGE.value,
                still_waiting(),
            )
            .order_by(q.created_at, q.message_id)
            .limit(limit + 1)
        )
    ).all()
    return [
        AskedInALine(
            pod_id=pod_id,
            channel_id=channel_id,
            message_id=message_id,
            prompt=_prompt(question),
            choices=offered_choices(question),
            since=created_at,
        )
        for pod_id, channel_id, message_id, question, created_at in rows[:limit]
    ], len(rows) > limit


__all__ = ["AskedInALine", "asked_in_a_line", "offered_choices"]
