"""A question's life when answering it goes wrong -- end to end, on Postgres.

An answer claims its question (OPEN -> ANSWERING) and commits that alone, then
hands it to whatever asked. Everything here is about the claim not outliving
the answer: an asker that fails any way puts the question back; a caller that
dies leaves a claim that lapses, which still waits on its person, may be
answered again, and is put back -- or expired -- by the sweeper; two answers at
once take effect once. And the boundary: an agent never answers an approval or
another conversation's paused question, by its tool or with a delegated token.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import WorkClass, WorkRegistry, facts
from app.core.infrastructure.work.testing import run_until_idle
from app.modules.channels.contracts import (
    Asker,
    NewLine,
    NewQuestion,
    Principal,
    QuestionAsker,
    QuestionKind,
    QuestionState,
    Sender,
    direct_channel,
    post,
)
from app.modules.channels.contracts import questions as channel_questions
from app.modules.channels.contracts.questions import (
    AnswerHandler,
    Answering,
    AnswerRefused,
)
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.channels.events.member_left_work import register_member_left_work
from app.modules.channels.events.sweeps import questions_expire
from app.modules.channels.infrastructure.change_models import ChannelChangeModel
from app.modules.channels.infrastructure.models import ChannelQuestionModel
from app.modules.channels.infrastructure.questions import CLAIM_TIMEOUT
from app.modules.channels.services import questions as question_service
from app.modules.channels.tests.e2e.channel_pod import Pod, an_asker
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)
from app.modules.pod.contracts.lifecycle import PodMemberRemoved

pytestmark = [pytest.mark.e2e]

#: What answering these tests' questions -- asked as ``MESSAGE`` -- does is
#: the test's (``asker``).
_ASKERS: list[AnswerHandler] = []


async def _answered(answering: Answering) -> Mapping[str, object] | None:
    return await _ASKERS[-1](answering) if _ASKERS else None


@pytest.fixture(autouse=True)
def _message_answers(monkeypatch):
    monkeypatch.setitem(question_service._HANDLERS, QuestionAsker.MESSAGE, _answered)


@pytest.fixture
def asker():
    """Set what answering this test's questions does."""

    def use(handler: AnswerHandler) -> None:
        _ASKERS.append(handler)

    yield use
    _ASKERS.clear()


def _uows() -> SessionUnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


async def _ask(
    db_session: AsyncSession,
    pod: Pod,
    *,
    awaits: dict | None = None,
    kind: QuestionKind = QuestionKind.ASK,
    ref: str | None = None,
    asked_by: Asker | None = None,
    expires_at: datetime | None = None,
) -> UUID:
    """A question in the awaited member's own channel, asked by ``asked_by``
    (a fresh conversation's message by default); the line's id."""
    person = UUID((awaits or pod.owner)["id"])
    asked_by = asked_by or await an_asker(UUID(pod.id), UUID(pod.owner["id"]))
    uow = SqlAlchemyUnitOfWork(db_session)
    channel = await direct_channel(uow, pod_id=UUID(pod.id), user_id=person)
    line = await post(
        uow,
        channel_id=channel.id,
        line=NewLine(
            sender=Sender.agent(UUID(pod.id), on_behalf_of=Principal.member(person)),
            text="Which day?",
        ),
        question=NewQuestion(
            kind=kind,
            ref=ref or f"e2elife:{uuid4()}",
            asker=asked_by,
            question={"prompt": "Which day?"},
            awaits=Principal.member(person),
            expires_at=expires_at,
        ),
    )
    await db_session.commit()
    assert line is not None
    return line.id


async def _claimed(message_id: UUID, *, ago: timedelta) -> None:
    """Leave the question claimed ``ago``, as a caller that died would."""
    async with async_session_maker() as session:
        await session.execute(
            update(ChannelQuestionModel)
            .where(ChannelQuestionModel.message_id == message_id)
            .values(state="ANSWERING", claimed_at=datetime.now(UTC) - ago)
        )
        await session.commit()


async def _state(message_id: UUID) -> QuestionState:
    async with async_session_maker() as session:
        state = await session.scalar(
            select(ChannelQuestionModel.state).where(
                ChannelQuestionModel.message_id == message_id
            )
        )
    assert state is not None
    return QuestionState(state)


async def _answer(pod: Pod, message_id: UUID, given: Mapping[str, object]):
    return await channel_questions.answer(
        _uows(),
        message_id=message_id,
        by=Principal.member(UUID(pod.owner["id"])),
        given=given,
    )


async def test_an_asker_that_fails_puts_the_question_back_to_be_answered_again(
    pod: Pod, db_session: AsyncSession, asker
):
    """Not a refusal -- the asker fell over. The question waits again rather
    than sitting claimed, and the next answer is taken."""
    tries: list[str] = []

    async def falls_over_once(answering: Answering) -> Mapping[str, object]:
        tries.append(str(answering.answer.get("text")))
        if len(tries) == 1:
            raise RuntimeError("the asker's database went away")
        return {"text": "Tuesday, confirmed"}

    asker(falls_over_once)
    asked = await _ask(db_session, pod)

    with pytest.raises(RuntimeError):
        await _answer(pod, asked, {"text": "Tuesday"})
    assert await _state(asked) is QuestionState.OPEN

    answered = await _answer(pod, asked, {"text": "Tuesday"})
    assert answered.state is QuestionState.ANSWERED
    assert answered.answer == {"text": "Tuesday, confirmed"}
    assert tries == ["Tuesday", "Tuesday"]


async def test_a_claim_whose_caller_died_still_waits_and_is_answered_again(
    pod: Pod, db_session: AsyncSession
):
    lapsed = await _ask(db_session, pod)
    fresh = await _ask(db_session, pod)
    await _claimed(lapsed, ago=CLAIM_TIMEOUT * 2)
    await _claimed(fresh, ago=timedelta(seconds=1))

    inbox = (await pod.client().get(f"/pods/{pod.id}/inbox")).json()
    assert inbox["totals"]["waiting"] == 2
    assert {item["message"]["id"] for item in inbox["waiting"]} == {
        str(lapsed),
        str(fresh),
    }

    with pytest.raises(AnswerRefused) as taken:
        await _answer(pod, fresh, {"text": "Monday"})
    assert taken.value.status_code == 409
    answered = await _answer(pod, lapsed, {"text": "Monday"})
    assert answered.state is QuestionState.ANSWERED


async def test_the_sweeper_puts_back_a_lapsed_claim_or_expires_it(
    pod: Pod, db_session: AsyncSession
):
    past = datetime.now(UTC) - timedelta(minutes=5)
    long_ago = CLAIM_TIMEOUT * 2
    reopened = await _ask(db_session, pod)
    overdue = await _ask(db_session, pod, expires_at=past)
    being_answered = await _ask(db_session, pod, expires_at=past)
    await _claimed(reopened, ago=long_ago)
    await _claimed(overdue, ago=long_ago)
    await _claimed(being_answered, ago=timedelta(seconds=1))
    async with async_session_maker() as session:
        rev_before = await session.scalar(
            select(func.max(ChannelChangeModel.rev)).where(
                ChannelChangeModel.message_id == reopened
            )
        )

    await questions_expire()

    assert await _state(reopened) is QuestionState.OPEN
    assert await _state(overdue) is QuestionState.EXPIRED
    # An answer in flight is let finish: its asker may be taking it now.
    assert await _state(being_answered) is QuestionState.ANSWERING
    async with async_session_maker() as session:
        rev_after = await session.scalar(
            select(func.max(ChannelChangeModel.rev)).where(
                ChannelChangeModel.message_id == reopened
            )
        )
    assert rev_after is not None and rev_before is not None
    assert rev_after > rev_before, "its watchers were not told it waits again"


async def test_two_answers_at_once_take_effect_once(
    pod: Pod, db_session: AsyncSession, asker
):
    taken: list[str] = []

    async def slow(answering: Answering) -> None:
        taken.append(str(answering.answer.get("text")))
        await asyncio.sleep(0.3)

    asker(slow)
    asked = await _ask(db_session, pod)

    outcomes = await asyncio.gather(
        _answer(pod, asked, {"text": "Monday"}),
        _answer(pod, asked, {"text": "Friday"}),
        return_exceptions=True,
    )

    refused = [o for o in outcomes if isinstance(o, AnswerRefused)]
    answered = [o for o in outcomes if not isinstance(o, BaseException)]
    assert len(refused) == 1 and refused[0].status_code == 409
    assert len(answered) == 1 and answered[0].state is QuestionState.ANSWERED
    assert taken == [str(answered[0].answer["text"])]


async def test_an_agent_answers_neither_an_approval_nor_another_conversations_pause(
    pod: Pod, db_session: AsyncSession
):
    conversation = (await an_asker(UUID(pod.id), UUID(pod.owner["id"]))).conversation_id
    assert conversation is not None
    paused = await _ask(
        db_session,
        pod,
        ref=f"tool:{conversation}:call-1",
        asked_by=Asker.tool(conversation, "call-1"),
    )
    approval = await _ask(db_session, pod, kind=QuestionKind.APPROVAL)
    for asked, given in ((paused, {"text": "yes"}), (approval, {"decision": "DENY"})):
        with pytest.raises(AnswerRefused) as refused:
            await channel_questions.answer(
                _uows(),
                message_id=asked,
                by=Principal.member(UUID(pod.owner["id"])),
                given=given,
                through_agent=True,
            )
        assert refused.value.status_code == 403
        assert await _state(asked) is QuestionState.OPEN


async def test_a_delegated_agent_token_cannot_answer_an_approval_over_http(
    pod: Pod, db_session: AsyncSession
):
    owner = pod.owner["id"]
    approval = await _ask(db_session, pod, kind=QuestionKind.APPROVAL)
    uow = SqlAlchemyUnitOfWork(db_session)
    channel = await direct_channel(uow, pod_id=UUID(pod.id), user_id=UUID(owner))
    url = f"{pod.base}/{channel.id}/messages/{approval}/answer"
    claims = build_delegation_claims(
        workload_type="agent",
        workload_id=UUID(pod.id),
        workload_name=DEFAULT_POD_AGENT_NAME,
        pod_id=UUID(pod.id),
        session_id=f"question-lifecycle-e2e-{uuid4().hex}",
        invoked_by_user_id=UUID(owner),
    )
    token = await get_user_token(UUID(owner), delegation_claims=claims)

    as_agent = await pod.scenario.async_client.post(
        url,
        json={"answer": {"decision": "APPROVE_ONCE"}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert as_agent.status_code == 403, as_agent.text
    assert await _state(approval) is QuestionState.OPEN

    by_hand = await pod.client().post(url, json={"answer": {"decision": "DENY"}})
    assert by_hand.status_code == 200, by_hand.text


@pytest.mark.drives_work
async def test_a_member_leaving_cancels_what_waits_on_them_where_it_is_watched(
    pod: Pod, db_session: AsyncSession
):
    """Cancelled through the question service: each line moves on its
    channel's change counter, so a reader's live socket shows it closed."""
    asked = await _ask(db_session, pod, awaits=pod.editor)
    async with async_session_maker() as session:
        rev_before = await session.scalar(
            select(func.max(ChannelChangeModel.rev)).where(
                ChannelChangeModel.message_id == asked
            )
        )
    uow = SqlAlchemyUnitOfWork(db_session)
    await facts.emit(
        uow, PodMemberRemoved(pod_id=UUID(pod.id), user_id=UUID(pod.editor["id"]))
    )
    await db_session.commit()
    registry = WorkRegistry()
    register_member_left_work(registry)
    await run_until_idle(
        async_session_maker, registry=registry, classes=[WorkClass.EVENTS]
    )

    assert await _state(asked) is QuestionState.CANCELLED
    async with async_session_maker() as session:
        rev_after = await session.scalar(
            select(func.max(ChannelChangeModel.rev)).where(
                ChannelChangeModel.message_id == asked
            )
        )
    assert rev_before is not None and rev_after is not None
    assert rev_after > rev_before


async def test_a_question_whose_asking_conversation_is_deleted_is_cancelled(
    pod: Pod, db_session: AsyncSession
):
    """Nobody is left to take its answer: the sweeper cancels it, and
    announces the close, rather than leave it waiting on its person."""
    asked_by = await an_asker(UUID(pod.id), UUID(pod.owner["id"]))
    orphaned = await _ask(db_session, pod, asked_by=asked_by)
    kept = await _ask(db_session, pod)
    async with async_session_maker() as session:
        await session.execute(
            delete(ConversationModel).where(
                ConversationModel.id == asked_by.conversation_id
            )
        )
        await session.commit()

    await questions_expire()

    assert await _state(orphaned) is QuestionState.CANCELLED
    assert await _state(kept) is QuestionState.OPEN


async def test_an_asker_withdraws_only_what_it_asked(
    pod: Pod, db_session: AsyncSession
):
    """Cancelled by the asking conversation's id, not by matching refs: a ref
    that merely starts the same way is somebody else's question."""
    mine = await an_asker(UUID(pod.id), UUID(pod.owner["id"]))
    theirs = await an_asker(UUID(pod.id), UUID(pod.owner["id"]))
    assert mine.conversation_id is not None
    asked = await _ask(
        db_session, pod, ref=f"msg:{mine.conversation_id}:1", asked_by=mine
    )
    lookalike = await _ask(
        db_session, pod, ref=f"msg:{mine.conversation_id}:2", asked_by=theirs
    )

    async with _uows()() as uow:
        cancelled = await channel_questions.cancel_asked_by(
            uow, QuestionAsker.MESSAGE, conversation_id=mine.conversation_id
        )
        await uow.commit()

    assert [question.message_id for question in cancelled] == [asked]
    assert await _state(asked) is QuestionState.CANCELLED
    assert await _state(lookalike) is QuestionState.OPEN
