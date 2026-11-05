"""How an ask ends, and who it may be offered to, against real conversations.

Each outcome -- ANSWERED, NO_ANSWER, UNFINISHED, DISCONNECTED -- is delivered
with its mode, its outcome and, for an answer, the offset of the other pod's
own words, by both delivery paths: inline to an asker still waiting in its tool
call, and by the completion event to one that ended its turn. Runs are started
with the enqueue suppressed and finished by hand, which is all delivery reads.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from uuid import UUID

import pytest
from fastapi import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.context import ApprovedExecution
from app.modules.agent.domain.pod_asks import ANSWER_SOURCE, AskMode, AskRefused
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.infrastructure.pod_ask_queries import PodAskQueries
from app.modules.agent.infrastructure.pod_link_queries import PodLinkQueries
from app.modules.agent.infrastructure.repositories import ConversationRepository
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.tests.e2e.pod_link_helpers import (
    ask_service,
    asking_deps,
    connect,
    delivery,
    finish_latest_run,
    messages,
    task_conversation,
    teammate,
)
from app.modules.agent.tests.e2e.test_agent_e2e import _create_test_pod
from app.modules.agent.tools.messaging.teammates import (
    AskTeammateRequest,
    ask_teammate,
    list_teammates,
)
from app.modules.test_support.e2e.waiters import eventually

pytestmark = pytest.mark.e2e


async def _linked(client, org) -> tuple[str, str]:
    """Support and Billing, with Billing letting Support ask it."""
    support = await _create_test_pod(client, org)
    billing = await _create_test_pod(client, org)
    linked = await connect(client, answering=billing, asking=support)
    assert linked.status_code == status.HTTP_204_NO_CONTENT, linked.text
    return support, billing


async def _link_deps(client, org, user, support: str):
    return asking_deps(
        user_id=UUID(user["id"]),
        org_id=UUID(org["id"]),
        pod_id=support,
        conversation_id=await task_conversation(client, support),
    )


async def _ask(deps, billing: str, *, wait_seconds: float = 0):
    with suppress_agent_run_enqueue():
        return await ask_service().ask(
            deps,
            teammate=await teammate(deps, billing),
            request="Anything overdue?",
            wait_seconds=wait_seconds,
        )


async def _delivered(asking: UUID):
    return [
        m
        for m in await messages(asking)
        if (m.metadata or {}).get("source") == ANSWER_SOURCE
    ]


async def _the_thread_once_opened(asking: UUID) -> UUID:
    async def probe() -> list[UUID]:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            return await PodAskQueries(uow).list_threads(from_conversation_id=asking)

    [thread_id] = await eventually(
        label="the ask's thread opened", probe=probe, done=bool, timeout_seconds=10
    )
    return thread_id


async def _finish(thread_id: UUID, words: str, *, disconnect: tuple[str, str] | None):
    """Finish the thread's run -- and, in the same commit, take the link away."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversations = ConversationRepository(uow)
        found = await conversations.get_conversation(thread_id, include_runs=True)
        assert found is not None
        run = found.agent_runs[-1]
        await conversations.append_message(
            conversation_id=thread_id,
            agent_run_id=run.id,
            draft=MessageDraft.of_text(words, role=MessageRole.ASSISTANT),
        )
        await conversations.finish_agent_run(
            agent_run_id=run.id,
            status=AgentRunStatus.COMPLETED,
            conversation_status=ConversationStatus.COMPLETED,
        )
        if disconnect is not None:
            answering, asking = disconnect
            await PodLinkQueries(uow).disconnect(
                answering_pod_id=UUID(answering), asking_pod_id=UUID(asking)
            )
        await uow.commit()


@pytest.mark.asyncio
async def test_an_inline_answer_is_exactly_the_other_pods_words(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _linked(authenticated_client, fixed_test_org)
    deps = await _link_deps(
        authenticated_client, fixed_test_org, fixed_test_user, support
    )
    words = "Two are overdue: inv-1 and inv-2."

    async def finisher() -> None:
        thread_id = await _the_thread_once_opened(deps.conversation_id)
        await _finish(thread_id, words, disconnect=None)

    outcome, _ = await asyncio.gather(_ask(deps, billing, wait_seconds=20), finisher())

    assert (outcome.status, outcome.mode) == ("ANSWERED", AskMode.LINK)
    assert outcome.answer == words
    assert "Its answer follows" in (outcome.detail or "")
    # Taken inline, so the completion path has nothing left to deliver.
    assert (
        await delivery().deliver_owed(asking_conversation_id=deps.conversation_id) == 0
    )
    assert await _delivered(deps.conversation_id) == []


@pytest.mark.asyncio
async def test_an_inline_answer_finished_as_the_link_goes_is_withheld(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _linked(authenticated_client, fixed_test_org)
    deps = await _link_deps(
        authenticated_client, fixed_test_org, fixed_test_user, support
    )

    async def finisher() -> None:
        thread_id = await _the_thread_once_opened(deps.conversation_id)
        await _finish(thread_id, "the payroll total", disconnect=(billing, support))

    outcome, _ = await asyncio.gather(_ask(deps, billing, wait_seconds=20), finisher())

    assert outcome.status == "DISCONNECTED"
    assert outcome.answer is None
    assert "payroll" not in (outcome.detail or "")


@pytest.mark.parametrize(
    ("words", "run_status", "error", "outcome", "said"),
    [
        ("Two are overdue.", AgentRunStatus.COMPLETED, None, "ANSWERED", None),
        (None, AgentRunStatus.COMPLETED, None, "NO_ANSWER", "without writing"),
        (None, AgentRunStatus.FAILED, "model timed out", "UNFINISHED", "timed out"),
    ],
)
@pytest.mark.asyncio
async def test_the_completion_path_delivers_each_outcome_once(
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    words,
    run_status,
    error,
    outcome,
    said,
):
    support, billing = await _linked(authenticated_client, fixed_test_org)
    deps = await _link_deps(
        authenticated_client, fixed_test_org, fixed_test_user, support
    )
    asked = await _ask(deps, billing)
    assert asked.status == "WORKING"
    run_id = await finish_latest_run(
        asked.conversation_id, words=words, status=run_status, error=error
    )

    with suppress_agent_run_enqueue():
        assert await delivery().settle(conversation_id=asked.conversation_id) == 1

    [message] = await _delivered(deps.conversation_id)
    metadata = message.metadata
    assert metadata["ask_outcome"] == outcome
    assert metadata["ask_mode"] == "LINK"
    assert metadata["ask_run_id"] == str(run_id)
    if outcome == "ANSWERED":
        assert message.text[metadata["answer_offset"] :] == words
    else:
        assert metadata["answer_offset"] is None
        assert said in message.text


@pytest.mark.asyncio
async def test_an_answer_finished_after_the_link_went_is_delivered_as_disconnected(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _linked(authenticated_client, fixed_test_org)
    deps = await _link_deps(
        authenticated_client, fixed_test_org, fixed_test_user, support
    )
    asked = await _ask(deps, billing)
    await finish_latest_run(asked.conversation_id, words="the payroll total")
    gone = await authenticated_client.delete(f"/pods/{billing}/pod-links/{support}")
    assert gone.status_code == status.HTTP_204_NO_CONTENT, gone.text

    with suppress_agent_run_enqueue():
        assert await delivery().settle(conversation_id=asked.conversation_id) == 1

    [message] = await _delivered(deps.conversation_id)
    assert message.metadata["ask_outcome"] == "DISCONNECTED"
    assert message.metadata["answer_offset"] is None
    assert "payroll" not in message.text


@pytest.mark.asyncio
async def test_disconnecting_stops_a_link_thread_still_running(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _linked(authenticated_client, fixed_test_org)
    deps = await _link_deps(
        authenticated_client, fixed_test_org, fixed_test_user, support
    )
    asked = await _ask(deps, billing)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        running = await ConversationRepository(uow).get_conversation(
            asked.conversation_id, include_runs=True
        )
    assert running is not None and running.status is ConversationStatus.RUNNING

    gone = await authenticated_client.delete(f"/pods/{billing}/pod-links/{support}")
    assert gone.status_code == status.HTTP_204_NO_CONTENT, gone.text

    async with create_uow_from_session_maker(async_session_maker) as uow:
        stopped = await ConversationRepository(uow).get_conversation(
            asked.conversation_id, include_runs=True
        )
    assert stopped is not None
    assert stopped.status is not ConversationStatus.RUNNING
    # STOPPED when no worker had claimed the turn yet (the stop drops it and
    # ends the run in its transaction), STOP_REQUESTED when one had.
    assert stopped.agent_runs[-1].status in {
        AgentRunStatus.STOPPED,
        AgentRunStatus.STOP_REQUESTED,
    }


@pytest.mark.asyncio
async def test_concurrent_deliveries_deliver_an_answer_exactly_once(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _linked(authenticated_client, fixed_test_org)
    deps = await _link_deps(
        authenticated_client, fixed_test_org, fixed_test_user, support
    )
    asked = await _ask(deps, billing)
    await finish_latest_run(asked.conversation_id, words="Two are overdue.")

    with suppress_agent_run_enqueue():
        counts = await asyncio.gather(
            *(
                delivery().deliver_owed(asking_conversation_id=deps.conversation_id)
                for _ in range(4)
            ),
            return_exceptions=True,
        )

    assert [c for c in counts if isinstance(c, BaseException)] == []
    assert sum(counts) == 1
    assert len(await _delivered(deps.conversation_id)) == 1


async def _three_pods(client, org) -> tuple[str, str, str]:
    """Support; Billing, connected and with the person in it; Legal, only the person."""
    support, billing = await _linked(client, org)
    legal = await _create_test_pod(client, org)
    return support, billing, legal


def _ctx(user, org, support: str, conversation_id: UUID, **extra):
    return SimpleNamespace(
        deps=asking_deps(
            user_id=UUID(user["id"]),
            org_id=UUID(org["id"]),
            pod_id=support,
            conversation_id=conversation_id,
            **extra,
        )
    )


@pytest.mark.asyncio
async def test_list_teammates_offers_only_what_this_conversation_could_ask(
    authenticated_client, fixed_test_org, fixed_test_user
):
    client = authenticated_client
    support, billing, legal = await _three_pods(client, fixed_test_org)
    chat = await client.post(
        f"/pods/{support}/conversations", json={"title": "mine", "type": "CHAT"}
    )
    assert chat.status_code == status.HTTP_201_CREATED, chat.text
    chat_id = UUID(chat.json()["id"])

    def reach(listed) -> dict[str, list[str]]:
        return {one.teammate: one.reach for one in listed.teammates}

    own = await list_teammates(_ctx(fixed_test_user, fixed_test_org, support, chat_id))
    assert reach(own) == {
        billing: ["through the person", "connected"],
        legal: ["through the person"],
    }

    task = await task_conversation(client, support)
    nobody = await list_teammates(_ctx(fixed_test_user, fixed_test_org, support, task))
    assert reach(nobody) == {billing: ["connected"]}

    channel = await list_teammates(
        _ctx(
            fixed_test_user,
            fixed_test_org,
            support,
            chat_id,
            surface_conversation_kind="CHANNEL",
        )
    )
    assert channel.teammates == []


@pytest.mark.parametrize("kind", ["CHANNEL", "EMAIL"])
@pytest.mark.asyncio
async def test_a_thread_others_read_cannot_ask_over_a_link_either(
    authenticated_client, fixed_test_org, fixed_test_user, kind
):
    support, billing = await _linked(authenticated_client, fixed_test_org)
    task = await task_conversation(authenticated_client, support)
    deps = asking_deps(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=support,
        conversation_id=task,
        surface_conversation_kind=kind,
    )
    linked = await teammate(deps, billing)
    assert linked.connected

    with pytest.raises(AskRefused, match="Other people read this thread"):
        await ask_service().ensure_may_ask(deps, teammate=linked)
    with pytest.raises(AskRefused, match="Other people read this thread"):
        await _ask(deps, billing)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        assert await PodAskQueries(uow).list_threads(from_conversation_id=task) == []


@pytest.mark.asyncio
async def test_only_an_approval_of_ask_teammate_itself_stands_for_the_persons_ok(
    authenticated_client, fixed_test_org, fixed_test_user
):
    """An approved call to some other tool does not let this one skip asking."""
    support = await _create_test_pod(authenticated_client, fixed_test_org)
    billing = await _create_test_pod(authenticated_client, fixed_test_org)
    chat = await authenticated_client.post(
        f"/pods/{support}/conversations", json={"title": "mine", "type": "CHAT"}
    )
    chat_id = UUID(chat.json()["id"])

    def approved(tool_name: str):
        return _ctx(
            fixed_test_user,
            fixed_test_org,
            support,
            chat_id,
            approved_execution=ApprovedExecution(
                approver_user_id=UUID(fixed_test_user["id"]),
                agent_id=None,
                conversation_id=chat_id,
                tool_name=tool_name,
            ),
        )

    request = AskTeammateRequest(teammate=billing, request="Who owns inv-1?")
    other = await ask_teammate(approved("pod_write_record"), request)
    assert other.success is False and other.needs_approval is True

    with suppress_agent_run_enqueue():
        this = await ask_teammate(
            approved("ask_teammate"),
            AskTeammateRequest(
                teammate=billing, request="Who owns inv-1?", wait_seconds=0
            ),
        )
    assert this.success is True, this
    assert (this.status, this.asked_as) == ("WORKING", "AS_PERSON")
