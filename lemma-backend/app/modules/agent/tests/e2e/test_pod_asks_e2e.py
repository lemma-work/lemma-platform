"""One pod asking another, against real conversations in two real pods.

The person here is a member of both pods, so the ask runs as them: a
conversation in the pod being asked, owned by them, carrying where it came from.
No worker and no model -- runs are started with the enqueue suppressed and
finished by hand, which is all the delivery rules look at.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.modules.agent.domain.pod_asks import (
    ANSWER_SOURCE,
    ASK_KEY,
    ASK_SOURCE,
    AskRefused,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    ConversationStatus,
    MessageDraft,
    MessageRole,
)
from app.core.authorization.delegation import DEFAULT_POD_AGENT_NAME
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.agent_context_brief import AgentContextBriefBuilder
from app.modules.agent.services.approval_reconciliation import (
    record_session_approvals,
)
from app.modules.agent.services.pod_ask_delivery import PodAskDelivery
from app.modules.agent.services.pod_ask_service import PodAskService
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.tests.e2e.test_agent_e2e import _create_test_pod
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.test_support.e2e.scripted_model import script_text, script_tool_call
from app.modules.test_support.e2e.waiters import eventually
from app.modules.test_support.e2e_authz import (
    add_pod_member,
    auth_headers,
    invite_org_member,
    signup_user,
)
from app.modules.agent.tools.messaging.teammates import (
    AskTeammateRequest,
    ask_permission_id,
    ask_teammate,
)

pytestmark = pytest.mark.e2e


def _service() -> PodAskService:
    return PodAskService(SessionUnitOfWorkFactory(async_session_maker))


def _delivery() -> PodAskDelivery:
    return PodAskDelivery(SessionUnitOfWorkFactory(async_session_maker))


async def _chat(client, pod_id: str) -> UUID:
    response = await client.post(
        f"/pods/{pod_id}/conversations", json={"title": "asks e2e", "type": "CHAT"}
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    return UUID(response.json()["id"])


def _deps(*, user_id: UUID, org_id: UUID, pod_id: str, conversation_id: UUID):
    return BaseAgentContext(
        user_id=user_id,
        org_id=org_id,
        pod_id=UUID(pod_id),
        conversation_id=conversation_id,
        is_pod_default_agent=True,
    )


async def _two_pods(client, org) -> tuple[str, str]:
    return await _create_test_pod(client, org), await _create_test_pod(client, org)


async def _teammate(deps: BaseAgentContext, pod_id: str):
    teammates = await _service().teammates(
        user_id=deps.user_id, organization_id=deps.org_id, pod_id=deps.pod_id
    )
    return next(one for one in teammates if str(one.pod_id) == pod_id)


async def _finish_with(conversation_id: UUID, *, words: str) -> UUID:
    """End the conversation's latest run having said ``words``, as a run would."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        conversations = ConversationRepository(uow)
        conversation = await conversations.get_conversation(
            conversation_id, include_runs=True
        )
        run = conversation.agent_runs[-1]
        await conversations.append_message(
            conversation_id=conversation_id,
            agent_run_id=run.id,
            draft=MessageDraft.of_text(words, role=MessageRole.ASSISTANT),
        )
        await conversations.finish_agent_run(
            agent_run_id=run.id,
            status=AgentRunStatus.COMPLETED,
            conversation_status=ConversationStatus.COMPLETED,
        )
        await uow.commit()
    return run.id


async def _messages(conversation_id: UUID):
    async with create_uow_from_session_maker(async_session_maker) as uow:
        messages, _ = await ConversationRepository(uow).list_messages(
            conversation_id=conversation_id, limit=50
        )
    return messages


async def _set_status(conversation_id: UUID, value: ConversationStatus) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await ConversationRepository(uow).set_conversation_status(
            conversation_id=conversation_id, status=value
        )
        await uow.commit()


@pytest.mark.asyncio
async def test_an_ask_runs_as_the_person_and_its_answer_arrives_once(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _two_pods(authenticated_client, fixed_test_org)
    asking = await _chat(authenticated_client, support)
    deps = _deps(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=support,
        conversation_id=asking,
    )
    teammate = await _teammate(deps, billing)

    with suppress_agent_run_enqueue():
        outcome = await _service().ask(
            deps, teammate=teammate, request="Why did invoice 42 fail?", wait_seconds=0
        )
    assert outcome.status == "WORKING"

    # The other pod has a conversation of the person's own, saying where the
    # request came from, with the request as its message.
    thread = await authenticated_client.get(
        f"/pods/{billing}/conversations/{outcome.conversation_id}"
    )
    assert thread.status_code == status.HTTP_200_OK, thread.text
    body = thread.json()
    assert body["metadata"]["source"] == ASK_SOURCE
    ask = body["metadata"][ASK_KEY]
    assert ask["from_conversation_id"] == str(asking)
    assert ask["for_user_id"] == fixed_test_user["id"]
    assert ask["chain"]["depth"] == 1
    requests = [m for m in await _messages(outcome.conversation_id) if m.role == "user"]
    assert [m.text for m in requests] == ["Why did invoice 42 fail?"]

    # A second ask while the first is open is refused rather than mixed in.
    with pytest.raises(AskRefused, match="still on your last request"):
        with suppress_agent_run_enqueue():
            await _service().ask(
                deps, teammate=teammate, request="And invoice 43?", wait_seconds=0
            )

    run_id = await _finish_with(outcome.conversation_id, words="The card expired.")
    with suppress_agent_run_enqueue():
        assert await _delivery().settle(conversation_id=outcome.conversation_id) == 1
        # Delivered once: the same completion arriving again delivers nothing.
        assert await _delivery().settle(conversation_id=outcome.conversation_id) == 0

    delivered = [
        m
        for m in await _messages(asking)
        if (m.metadata or {}).get("source") == ANSWER_SOURCE
    ]
    assert len(delivered) == 1
    assert "The card expired." in delivered[0].text
    assert delivered[0].metadata["ask_run_id"] == str(run_id)

    # The turn that answer started is not the person's: it may relay what came
    # back, but asking again -- here or anywhere -- waits for them to say so.
    with pytest.raises(AskRefused, match="not with something the person wrote"):
        with suppress_agent_run_enqueue():
            await _service().ask(
                deps, teammate=teammate, request="And invoice 43?", wait_seconds=0
            )


@pytest.mark.asyncio
async def test_an_answer_waits_while_the_asker_is_paused_on_the_person(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _two_pods(authenticated_client, fixed_test_org)
    asking = await _chat(authenticated_client, support)
    deps = _deps(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=support,
        conversation_id=asking,
    )
    with suppress_agent_run_enqueue():
        outcome = await _service().ask(
            deps,
            teammate=await _teammate(deps, billing),
            request="What changed this week?",
            wait_seconds=0,
        )
    await _finish_with(outcome.conversation_id, words="Two refunds.")

    # The asker is waiting on the person's approval; a message now would deny it.
    await _set_status(asking, ConversationStatus.WAITING)
    with suppress_agent_run_enqueue():
        assert await _delivery().settle(conversation_id=outcome.conversation_id) == 0

    # Once that pause is over, the asker's own finished run collects the answer.
    await _set_status(asking, ConversationStatus.COMPLETED)
    with suppress_agent_run_enqueue():
        assert await _delivery().settle(conversation_id=asking) == 1
    texts = [m.text for m in await _messages(asking)]
    assert any("Two refunds." in (text or "") for text in texts)


@pytest.mark.asyncio
async def test_a_client_cannot_aim_an_answer_at_another_conversation(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _two_pods(authenticated_client, fixed_test_org)
    asking = await _chat(authenticated_client, support)
    elsewhere = await _chat(authenticated_client, support)
    deps = _deps(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=support,
        conversation_id=asking,
    )
    with suppress_agent_run_enqueue():
        outcome = await _service().ask(
            deps,
            teammate=await _teammate(deps, billing),
            request="Anything overdue?",
            wait_seconds=0,
        )
    stored = (
        await authenticated_client.get(
            f"/pods/{billing}/conversations/{outcome.conversation_id}"
        )
    ).json()["metadata"][ASK_KEY]
    forged = {**stored, "from_conversation_id": str(elsewhere)}
    response = await authenticated_client.patch(
        f"/pods/{billing}/conversations/{outcome.conversation_id}",
        json={"metadata": {ASK_KEY: forged, "pinned": True}},
    )
    assert response.status_code == status.HTTP_200_OK, response.text
    assert response.json()["metadata"][ASK_KEY] == stored
    assert response.json()["metadata"]["pinned"] is True


@pytest.mark.asyncio
async def test_the_first_ask_to_a_pod_needs_the_persons_ok(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _two_pods(authenticated_client, fixed_test_org)
    asking = await _chat(authenticated_client, support)
    ctx = SimpleNamespace(
        deps=_deps(
            user_id=UUID(fixed_test_user["id"]),
            org_id=UUID(fixed_test_org["id"]),
            pod_id=support,
            conversation_id=asking,
        )
    )
    response = await ask_teammate(
        ctx,
        AskTeammateRequest(teammate=billing, request="Who owns invoice 42?"),
    )
    assert response.success is False
    assert response.needs_approval is True
    assert response.approval["tool_name"] == "ask_teammate"
    assert response.approval["permission_ids"] == [ask_permission_id(UUID(billing))]

    # Nothing was opened in the other pod while the person had not said yes.
    listing = await authenticated_client.get(f"/pods/{billing}/conversations")
    assert listing.status_code == status.HTTP_200_OK, listing.text
    assert all(
        (item.get("metadata") or {}).get("source") != ASK_SOURCE
        for item in listing.json()["items"]
    )


@pytest.mark.asyncio
async def test_a_pod_cannot_ask_itself_and_names_what_it_can_ask(
    authenticated_client, fixed_test_org, fixed_test_user
):
    support, billing = await _two_pods(authenticated_client, fixed_test_org)
    asking = await _chat(authenticated_client, support)
    ctx = SimpleNamespace(
        deps=_deps(
            user_id=UUID(fixed_test_user["id"]),
            org_id=UUID(fixed_test_org["id"]),
            pod_id=support,
            conversation_id=asking,
        )
    )
    itself = await ask_teammate(
        ctx, AskTeammateRequest(teammate=support, request="Hello?")
    )
    assert itself.success is False
    assert "No pod this one can ask is called" in itself.error


async def _approve_asks(
    conversation: dict[str, object], *, pod_id: str, user_id: UUID
) -> None:
    """What the person's "approve for this conversation" records, by the real path."""
    agent_id = conversation.get("agent_id")
    await record_session_approvals(
        conversation_id=UUID(str(conversation["id"])),
        agent_id=UUID(str(agent_id)) if agent_id else None,
        pod_id=UUID(str(conversation["pod_id"])),
        tool_args={
            "tool_name": "ask_teammate",
            "args": {},
            "permission_ids": [ask_permission_id(UUID(pod_id))],
        },
        user_id=user_id,
    )


async def _send(client, pod_id: str, conversation_id: UUID, content: str) -> None:
    async with client.stream(
        "POST",
        f"/pods/{pod_id}/conversations/{conversation_id}/messages",
        json={"content": content},
        timeout=60,
    ) as response:
        assert response.status_code == status.HTTP_200_OK, await response.aread()
        await response.aread()


@pytest.mark.asyncio
async def test_a_quick_answer_comes_back_in_the_same_call(
    authenticated_client, fixed_test_org, fixed_test_user, worker
):
    """The asking model calls the tool; the worker runs the other pod; it waits."""
    del worker
    support, billing = await _two_pods(authenticated_client, fixed_test_org)
    response = await authenticated_client.post(
        f"/pods/{support}/conversations",
        json={
            "type": "CHAT",
            "metadata": {
                "mock_llm_script": [
                    # Asking another pod sits behind tool search, like the rest
                    # of messaging, so it is found before it is called.
                    script_tool_call(
                        "search_tools",
                        {"queries": ["ask another pod"]},
                        tool_call_id="search-1",
                    ),
                    script_tool_call(
                        "ask_teammate",
                        {
                            "teammate": billing,
                            "request": "What is on the billing calendar?",
                            "wait_seconds": 40,
                        },
                        tool_call_id="ask-1",
                    ),
                    script_text("Billing answered."),
                ]
            },
        },
    )
    assert response.status_code == status.HTTP_201_CREATED, response.text
    asking = UUID(response.json()["id"])
    await _approve_asks(
        response.json(), pod_id=billing, user_id=UUID(fixed_test_user["id"])
    )

    await _send(authenticated_client, support, asking, "Ask billing what's on.")

    returns = [
        m
        for m in await _messages(asking)
        if m.tool_name == "ask_teammate" and m.tool_result
    ]
    assert returns, "the ask never returned"
    raw = returns[-1].tool_result
    result = json.loads(raw) if isinstance(raw, str) else raw
    assert result["status"] == "ANSWERED", result
    # The other pod's assistant ran unscripted, so its answer echoes the request.
    assert "What is on the billing calendar?" in result["answer"]
    # Taken inline, so nothing was delivered again as a new turn.
    assert not [
        m
        for m in await _messages(asking)
        if (m.metadata or {}).get("source") == ANSWER_SOURCE
    ]


@pytest.mark.asyncio
async def test_a_slow_answer_arrives_as_a_new_turn(
    authenticated_client, fixed_test_org, fixed_test_user, worker
):
    """The other pod finishes on the worker; its completion starts the asker's turn."""
    del worker
    support, billing = await _two_pods(authenticated_client, fixed_test_org)
    asking = await _chat(authenticated_client, support)
    deps = _deps(
        user_id=UUID(fixed_test_user["id"]),
        org_id=UUID(fixed_test_org["id"]),
        pod_id=support,
        conversation_id=asking,
    )
    outcome = await _service().ask(
        deps,
        teammate=await _teammate(deps, billing),
        request="Summarise this week's refunds.",
        wait_seconds=0,
    )
    assert outcome.status == "WORKING"

    async def _answered_and_read():
        messages = await _messages(asking)
        delivered = [
            m for m in messages if (m.metadata or {}).get("source") == ANSWER_SOURCE
        ]
        replies = [m for m in messages if m.role == "assistant" and m.text]
        return delivered, replies

    delivered, _ = await eventually(
        label="the answer delivered and the asker's turn run",
        probe=_answered_and_read,
        done=lambda seen: bool(seen[0]) and bool(seen[1]),
        timeout_seconds=60,
    )
    assert len(delivered) == 1
    assert "Summarise this week's refunds." in delivered[0].text
    assert delivered[0].metadata["ask_conversation_id"] == str(outcome.conversation_id)


@pytest.mark.asyncio
async def test_who_a_pod_can_ask_is_the_other_pods_the_person_is_in(
    authenticated_client, async_client, fixed_test_org, fixed_test_user
):
    """Per person: the page, the brief and the tool all read the same list."""
    support, billing = await _two_pods(authenticated_client, fixed_test_org)

    listed = await authenticated_client.get(f"/pods/{support}/askable-pods")
    assert listed.status_code == status.HTTP_200_OK, listed.text
    items = {item["pod_id"]: item for item in listed.json()["items"]}
    assert billing in items
    assert support not in items
    assert items[billing]["through_you"] is True
    assert items[billing]["connected"] is False

    # The assistant is told, without being asked to look. The pods are new, so
    # this first brief for them is built, not read from the brief cache.
    brief = await _default_agent_brief(pod_id=support, user_id=fixed_test_user["id"])
    assert "## Other pods you can ask" in brief
    assert f"(teammate: {billing})" in brief

    # Someone who is only in Support has nobody to ask through it.
    colleague = await signup_user(async_client, f"colleague-{uuid4().hex[:6]}")
    org_member = await invite_org_member(
        authenticated_client, async_client, org_id=fixed_test_org["id"], user=colleague
    )
    await add_pod_member(
        authenticated_client,
        pod_id=support,
        organization_member_id=org_member["id"],
        role="POD_USER",
        roles=["POD_USER"],
    )
    theirs = await async_client.get(
        f"/pods/{support}/askable-pods", headers=auth_headers(colleague)
    )
    assert theirs.status_code == status.HTTP_200_OK, theirs.text
    assert theirs.json()["items"] == []


async def _default_agent_brief(*, pod_id: str, user_id: str) -> str:
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    async with uow_factory() as uow:
        agent = await AgentRepository(uow).get_by_pod_and_name(
            pod_id=UUID(pod_id), name=DEFAULT_POD_AGENT_NAME
        )
    assert agent is not None
    conversation = SimpleNamespace(id=uuid4(), metadata={})
    return await AgentContextBriefBuilder(uow_factory).build(
        agent=agent,
        conversation=conversation,
        user_id=UUID(user_id),
        pod_id=UUID(pod_id),
    )
