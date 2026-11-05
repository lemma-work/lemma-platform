"""Answering a question a line asks, and closing it however it ends.

A question is asked by another module -- an agent's paused ``ask_user`` or
``request_approval`` (``TOOL``), a workflow's form (``WORKFLOW``), a question
one member's agent put to another (``MESSAGE``). Each registers what taking an
answer does (``answers_for``); channels cannot import those modules.

Answering, in order:

1. Only the person it waits on answers it; an approval needs a ``decision``,
   and is answered by a person's own tap -- never by an agent on their behalf.
2. The question is **claimed** (OPEN → ANSWERING) and that is committed, so a
   second answer -- another tap, a reply on a platform -- finds it taken.
3. The asker takes the answer and says what took effect. If it refuses -- or
   fails any other way -- the question is released: it waits again, and the
   person is told why. A caller that dies holding the claim leaves it to lapse
   (``CLAIM_TIMEOUT``): the next answer may take it, and the sweeper puts it
   back -- or expires it, if its time ran out meanwhile.
4. The question closes with the answer that took effect, and is announced.

A question answered somewhere else -- in the conversation, on a platform's
button -- is closed here by the module that saw it (``close``). One whose time
ran out is expired by a sweeper; one whose asking conversation or run was
deleted is cancelled by it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.infrastructure.work import facts
from app.modules.channels.contracts.facts import ChannelQuestionClosed
from app.modules.channels.domain.entities import (
    Principal,
    Question,
    QuestionAsker,
    QuestionKind,
    QuestionState,
)
from app.modules.channels.infrastructure.questions import QuestionRepository
from app.modules.channels.infrastructure.rows import now

#: The decisions an approval takes.
APPROVAL_DECISIONS = frozenset({"APPROVE_ONCE", "APPROVE_FOR_SESSION", "DENY"})


class AnswerRefused(Exception):
    """The answer was not taken; the reason is the person's to read."""

    def __init__(self, message: str, *, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass(frozen=True, slots=True)
class Answering:
    """One person answering one question, as its asker sees it."""

    pod_id: UUID
    question: Question
    answer: Mapping[str, object]
    by: Principal

    @property
    def ref(self) -> str:
        return self.question.ref


#: Takes an answer; returns the answer that took effect, or ``None`` for the
#: one given. Raises ``AnswerRefused`` to refuse it.
AnswerHandler = Callable[[Answering], Awaitable[Mapping[str, object] | None]]

_HANDLERS: dict[QuestionAsker, AnswerHandler] = {}  # memory: bounded per asker


def answers_for(asker: QuestionAsker) -> Callable[[AnswerHandler], AnswerHandler]:
    """Register what answering ``asker``'s questions does. Once per asker."""

    def register(handler: AnswerHandler) -> AnswerHandler:
        existing = _HANDLERS.get(asker)
        if existing is not None and existing is not handler:
            raise ValueError(f"Questions of {asker!r} already have an answerer")
        _HANDLERS[asker] = handler
        return handler

    return register


def answerer(question: Question) -> AnswerHandler | None:
    return _HANDLERS.get(question.asker)


def registered_askers() -> frozenset[QuestionAsker]:
    return frozenset(_HANDLERS)


def _checked(
    question: Question | None,
    by: Principal,
    answer: Mapping[str, object],
    *,
    through_agent: bool,
) -> Question:
    if question is None:
        raise AnswerRefused("There is no question here", status_code=404)
    if question.awaits != by:
        raise AnswerRefused(
            "This question is somebody else's to answer", status_code=403
        )
    if through_agent and question.asker is QuestionAsker.TOOL:
        # A paused ``ask_user`` belongs to the conversation that asked it: an
        # agent answering it from another conversation would answer for the
        # person in a run they are not in.
        raise AnswerRefused(
            "This is another conversation's question: the person answers it there",
            status_code=403,
        )
    if question.kind is QuestionKind.APPROVAL:
        if through_agent:
            raise AnswerRefused(
                "An approval is answered by the person it asks, not an agent",
                status_code=403,
            )
        if answer.get("decision") not in APPROVAL_DECISIONS:
            raise AnswerRefused(
                "An approval needs a decision: APPROVE_ONCE, APPROVE_FOR_SESSION or DENY",
                status_code=422,
            )
    if not question.is_waiting:
        raise AnswerRefused("This question is not waiting any more")
    # Still ANSWERING is the claim's to decide: taken now, or lapsed and free.
    return question


async def answer(
    uow_factory: UnitOfWorkFactory,
    *,
    message_id: UUID,
    by: Principal,
    given: Mapping[str, object],
    through_agent: bool = False,
) -> Question:
    """Answer a question as ``by``; returns it closed with what took effect."""
    async with uow_factory() as uow:
        repository = QuestionRepository(uow.session)
        question = _checked(
            await repository.of_message(message_id),
            by,
            given,
            through_agent=through_agent,
        )
        claimed = await repository.claim(question.message_id, at=now())
        await uow.commit()
    if claimed is None:
        raise AnswerRefused("Somebody is answering this already")
    handler = answerer(claimed)
    effective: Mapping[str, object] | None = None
    if handler is not None:
        taken = False
        try:
            effective = await handler(
                Answering(pod_id=claimed.pod_id, question=claimed, answer=given, by=by)
            )
            taken = True
        finally:
            if not taken:
                # Refused, broken or cancelled: either way nothing took effect
                # that the question records, so it waits again rather than
                # sitting claimed. (An asker that did take it and failed after
                # is idempotent: the next answer finds the decision it stored.)
                async with uow_factory() as uow:
                    await QuestionRepository(uow.session).release(claimed.message_id)
                    await uow.commit()
    async with uow_factory() as uow:
        closed = await close(
            uow,
            message_id=claimed.message_id,
            state=QuestionState.ANSWERED,
            answer=effective if effective is not None else given,
            by=by,
        )
        await uow.commit()
    if closed is not None:
        return closed
    async with uow_factory() as uow:
        # Closed meanwhile by the asker as it took the answer: read it as it is.
        current = await QuestionRepository(uow.session).of_message(claimed.message_id)
    assert current is not None
    return current


async def close(
    uow: SqlAlchemyUnitOfWork,
    *,
    message_id: UUID,
    state: QuestionState,
    answer: Mapping[str, object] | None = None,
    by: Principal | None = None,
) -> Question | None:
    """Close a question, and announce it; nothing if it was closed already."""
    closed = await QuestionRepository(uow.session).settle(
        message_id, state=state, answer=answer, by=by
    )
    if closed is not None:
        await announce_closed(uow, closed)
    return closed


async def close_ref(
    uow: SqlAlchemyUnitOfWork,
    *,
    pod_id: UUID,
    ref: str,
    state: QuestionState,
    answer: Mapping[str, object] | None = None,
    by: Principal | None = None,
) -> Question | None:
    found = await QuestionRepository(uow.session).by_ref(pod_id=pod_id, ref=ref)
    if found is None:
        return None
    return await close(
        uow, message_id=found.message_id, state=state, answer=answer, by=by
    )


async def cancel_asked_by(
    uow: SqlAlchemyUnitOfWork,
    asker: QuestionAsker,
    *,
    conversation_id: UUID | None = None,
    workflow_run_id: UUID | None = None,
) -> list[Question]:
    """Cancel every question still waiting that this conversation, or this
    run, asked -- the asker withdrew it."""
    cancelled = await QuestionRepository(uow.session).cancel_asked_by(
        asker, conversation_id=conversation_id, workflow_run_id=workflow_run_id
    )
    for question in cancelled:
        await announce_closed(uow, question)
    return cancelled


async def cancel_orphaned(uow: SqlAlchemyUnitOfWork, *, limit: int = 100) -> int:
    """Cancel the questions whose asking conversation or run was deleted --
    nothing is left to take an answer -- and announce each; how many."""
    cancelled = await QuestionRepository(uow.session).cancel_orphaned(limit=limit)
    for question in cancelled:
        await announce_closed(uow, question)
    return len(cancelled)


async def cancel_waiting_on(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID, user_id: UUID
) -> list[Question]:
    """Cancel every question waiting on this member -- they left the pod."""
    cancelled = await QuestionRepository(uow.session).cancel_awaiting(
        pod_id=pod_id, user_id=user_id
    )
    for question in cancelled:
        await announce_closed(uow, question)
    return cancelled


async def expire_overdue(uow: SqlAlchemyUnitOfWork, *, limit: int = 100) -> int:
    """Expire the questions whose time is up; how many."""
    expired = await QuestionRepository(uow.session).expire_due(at=now(), limit=limit)
    for question in expired:
        await announce_closed(uow, question)
    return len(expired)


async def release_lapsed(uow: SqlAlchemyUnitOfWork, *, limit: int = 100) -> int:
    """Put back the questions whose claim lapsed -- the caller that took one
    died answering it -- so they wait again; how many."""
    return await QuestionRepository(uow.session).release_lapsed(at=now(), limit=limit)


async def announce_closed(uow: SqlAlchemyUnitOfWork, question: Question) -> None:
    asked = question.asked_by
    await facts.emit(
        uow,
        ChannelQuestionClosed(
            pod_id=question.pod_id,
            channel_id=question.channel_id,
            message_id=question.message_id,
            ref=question.ref,
            batch_id=question.batch_id,
            state=question.state,
            awaited_user_id=question.awaits.user_id,
            asker=question.asker,
            asker_conversation_id=asked.conversation_id if asked else None,
            asker_workflow_run_id=asked.workflow_run_id if asked else None,
            asker_key=asked.key if asked else None,
        ),
    )
