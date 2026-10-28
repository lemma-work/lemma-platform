"""A provider that reuses tool call ids cannot make two pauses one.

A pause is addressed by its tool call id for as long as it is open -- the
approval card, the decision row, the return the next run replays -- and every
one of those lookups is per conversation. A provider that numbers its calls
(`call_0`) hands a later run the id of a question already answered: the new
card vanished behind the old return, and a decision adopted the old one.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.pausing_tools import USER_PAUSING_TOOL_NAMES
from app.modules.agent.domain.value_objects import (
    AgentEvent,
    AgentEventType,
    AgentRunApprovalDecision,
    ConversationStatus,
    MessageDraft,
    MessageKind,
)
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.run_event_pump import RunEventPump, RunOutcome
from app.modules.agent.services.run_identity import RunIdentity
from app.modules.agent.services.run_message_writer import RunMessageWriter
from app.modules.agent.tests.e2e.lifecycle_helpers import (
    conversation,
    conversation_service,
    finish,
    messages,
    owner_uow,
    running_run,
    runs,
    seeded_conversation,
    uow_factory,
)

pytestmark = [pytest.mark.e2e]

_REUSED = "call_0"


class _Finalizer:
    async def finish(self, **kwargs: object) -> None:
        del kwargs


class _Surface:
    """Records what a surface would render its card from."""

    def __init__(self) -> None:
        self.waiting: list[dict[str, object]] = []

    async def on_run_started(self, conversation, ctx) -> None: ...

    async def on_event(self, event, conversation, ctx) -> None:
        if event.type is AgentEventType.WAITING:
            self.waiting.append(dict(event.data))

    async def on_run_finished(self, conversation, ctx) -> None: ...

    async def on_run_failed(self, conversation, error) -> None: ...


async def _asks_again(run_id) -> AsyncIterator[AgentEvent]:
    """A run that asks a question under an id an earlier run already used."""
    yield AgentEvent(
        type=AgentEventType.MESSAGE,
        data=MessageDraft.of_tool_call(
            tool_name="ask_user",
            tool_call_id=_REUSED,
            tool_args={"questions": ["And the colour?"]},
        ),
        agent_run_id=run_id,
    )
    yield AgentEvent(
        type=AgentEventType.WAITING,
        data={"tool_call_id": _REUSED, "kind": "ask_user"},
        agent_run_id=run_id,
    )


async def test_a_reused_provider_id_is_a_new_pause_with_its_own_answer(scenario):
    seeded = await seeded_conversation(scenario)
    first = await running_run(seeded)
    # An earlier run's question, under the provider's id, answered.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repository = ConversationRepository(uow)
        await repository.append_message(
            conversation_id=seeded.conversation_id,
            agent_run_id=first,
            draft=MessageDraft.of_tool_call(
                tool_name="ask_user", tool_call_id=_REUSED, tool_args={}
            ),
        )
        await uow.commit()
    await finish(first, conversation_status=ConversationStatus.WAITING)
    async with owner_uow(seeded) as uow:
        await conversation_service(uow).resolve_user_approval_internal(
            conversation=await conversation(seeded.conversation_id),
            approval_id=_REUSED,
            user_id=seeded.user_id,
            pod_id=seeded.pod_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={"answers": {"size": "large"}},
        )

    # Answering it started the run that resumes; that run asks again.
    second = (await runs(seeded.conversation_id))[-1].id
    assert second != first
    surface = _Surface()
    await RunEventPump(RunMessageWriter(uow_factory()), _Finalizer()).drive(
        _asks_again(second),
        run=RunIdentity(conversation_id=seeded.conversation_id, agent_run_id=second),
        outcome=RunOutcome(),
        observer=surface,
        conversation=await conversation(seeded.conversation_id),
        ctx=None,
    )
    await finish(second, conversation_status=ConversationStatus.WAITING)

    asked = [
        m
        for m in await messages(seeded.conversation_id)
        if m.kind is MessageKind.TOOL_CALL and m.agent_run_id == second
    ]
    (renamed,) = [m.tool_call_id for m in asked]
    assert renamed and renamed != _REUSED
    # The surface is told the id the pause is stored under, so its card answers
    # this question rather than the old one.
    assert [w["tool_call_id"] for w in surface.waiting] == [renamed]
    async with create_uow_from_session_maker(async_session_maker) as uow:
        open_now = await ConversationRepository(uow).pausing_calls_awaiting_a_return(
            conversation_id=seeded.conversation_id,
            pausing_tool_names=USER_PAUSING_TOOL_NAMES,
        )
    assert renamed in [m.tool_call_id for m in open_now], "the new card vanished"

    async with owner_uow(seeded) as uow:
        answered = await conversation_service(uow).resolve_user_approval_internal(
            conversation=await conversation(seeded.conversation_id),
            approval_id=renamed,
            user_id=seeded.user_id,
            pod_id=seeded.pod_id,
            decision=AgentRunApprovalDecision.APPROVE_ONCE,
            response={"answers": {"colour": "blue"}},
        )
    assert answered.status == "resolved", "a decision adopted the old one"
    returns = {
        m.tool_call_id: m.tool_result
        for m in await messages(seeded.conversation_id)
        if m.kind is MessageKind.TOOL_RETURN
    }
    assert returns[_REUSED]["answers"] == {"size": "large"}
    assert returns[renamed]["answers"] == {"colour": "blue"}


async def _searches_again(run_id) -> AsyncIterator[AgentEvent]:
    """A run whose provider numbers every call ``call_0``: twice in one run."""
    for query in ("weather", "news"):
        yield AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_call(
                tool_name="web_search", tool_call_id=_REUSED, tool_args={"q": query}
            ),
            agent_run_id=run_id,
        )
        yield AgentEvent(
            type=AgentEventType.MESSAGE,
            data=MessageDraft.of_tool_return(
                tool_name="web_search",
                tool_call_id=_REUSED,
                tool_result={"found": query},
            ),
            agent_run_id=run_id,
        )


async def test_a_reused_id_on_any_call_pairs_each_result_with_its_own_call(scenario):
    """Results are paired with calls by id across the conversation -- the
    transcript's cards, history, a synthesized return -- so a call reusing an
    id showed an earlier call's result."""
    seeded = await seeded_conversation(scenario)
    first = await running_run(seeded)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        repository = ConversationRepository(uow)
        await repository.append_message(
            conversation_id=seeded.conversation_id,
            agent_run_id=first,
            draft=MessageDraft.of_tool_call(
                tool_name="web_search", tool_call_id=_REUSED, tool_args={"q": "old"}
            ),
        )
        await repository.append_message(
            conversation_id=seeded.conversation_id,
            agent_run_id=first,
            draft=MessageDraft.of_tool_return(
                tool_name="web_search",
                tool_call_id=_REUSED,
                tool_result={"found": "old"},
            ),
        )
        await uow.commit()
    await finish(first)
    second = await running_run(seeded)

    await RunEventPump(RunMessageWriter(uow_factory()), _Finalizer()).drive(
        _searches_again(second),
        run=RunIdentity(conversation_id=seeded.conversation_id, agent_run_id=second),
        outcome=RunOutcome(),
        observer=_Surface(),
        conversation=await conversation(seeded.conversation_id),
        ctx=None,
    )

    stored = [m for m in await messages(seeded.conversation_id) if m.tool_call_id]
    calls = {
        m.tool_call_id: m.tool_args["q"]
        for m in stored
        if m.kind is MessageKind.TOOL_CALL
    }
    results = {
        m.tool_call_id: m.tool_result["found"]
        for m in stored
        if m.kind is MessageKind.TOOL_RETURN
    }
    assert len(calls) == 3, "every call has an id of its own"
    assert results == calls
