"""Questions a line puts to one person: what other modules ask, answer and close.

An asker -- an agent's paused tool call (``TOOL``), a workflow's form
(``WORKFLOW``), a question one member's agent put to another (``MESSAGE``) --
posts a line with a ``NewQuestion`` naming itself (``Asker``)
(``channels.contracts.post``), registers what taking an answer does
(``answers_for``), and closes questions answered or ended elsewhere
(``close_ref``, ``cancel_asked_by``). ``ChannelQuestionClosed`` announces
every close, with the asker's type and ids.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.channels.domain.access import Viewer
from app.modules.channels.domain.entities import Asker, Question, QuestionAsker
from app.modules.channels.infrastructure.questions import (
    BatchProgress,
    QuestionRepository,
)
from app.modules.channels.services.questions import (
    APPROVAL_DECISIONS,
    AnswerHandler,
    Answering,
    AnswerRefused,
    answer,
    answers_for,
    cancel_asked_by,
    close,
    close_ref,
    expire_overdue,
    registered_askers,
)

__all__ = [
    "APPROVAL_DECISIONS",
    "Asker",
    "QuestionAsker",
    "BatchProgress",
    "AnswerHandler",
    "AnswerRefused",
    "Answering",
    "answer",
    "answers_for",
    "cancel_asked_by",
    "close",
    "close_ref",
    "expire_overdue",
    "batch_progress",
    "question_by_ref",
    "question_of",
    "questions_asked_by",
    "registered_askers",
    "waiting_on",
]


async def question_by_ref(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, ref: str
) -> Question | None:
    return await QuestionRepository(uow.session).by_ref(pod_id=pod_id, ref=ref)


async def question_of(uow: SqlAlchemyUnitOfWork, message_id: UUID) -> Question | None:
    return await QuestionRepository(uow.session).of_message(message_id)


async def questions_asked_by(
    uow: SqlAlchemyUnitOfWork,
    asker: QuestionAsker,
    *,
    conversation_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
    open_only: bool = False,
    limit: int = 100,
) -> list[Question]:
    """What this conversation, or this run, asked as ``asker``, newest first."""
    return await QuestionRepository(uow.session).asked_by(
        asker,
        conversation_id=conversation_id,
        workflow_run_id=workflow_run_id,
        open_only=open_only,
        limit=limit,
    )


async def batch_progress(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, batch_id: str
) -> BatchProgress:
    """How many questions were sent together, and how many still wait."""
    return await QuestionRepository(uow.session).batch_progress(
        pod_id=pod_id, batch_id=batch_id
    )


async def waiting_on(
    uow: SqlAlchemyUnitOfWork, viewer: Viewer, *, limit: int = 100
) -> list[Question]:
    """The questions waiting on this member, in channels they may see."""
    return await QuestionRepository(uow.session).waiting_on(viewer, limit=limit)
