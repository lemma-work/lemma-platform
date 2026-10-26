"""What is waiting on a person, across their conversations in a pod.

`GET /pods/{pod_id}/conversations/pending-interactions` and the
`has_pending_interaction` flag on a conversation answer the same question: is
there a question, an approval or an Agent Host permission request here that only
this person can answer. The web client used to answer it with a request per
WAITING conversation, and could not see a permission request at all, because
its conversation stays RUNNING while the host waits.

Against Postgres, because every rule here is a SQL predicate: undecided, not
returned, in a conversation still able to resume, owned by the caller, and --
for a permission request -- not older than the host is willing to wait.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.agent_host.domain.permissions import AGENT_HOST_PERMISSION_KEY
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    ConversationStatus,
    MessageDraft,
)
from app.modules.agent.infrastructure.models import (
    AgentPauseModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.infrastructure.db.pauses import Pauses
from app.modules.agent.tests.e2e.lifecycle_helpers import opening_for
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.db.runs import Runs
from app.modules.identity.infrastructure.models.user_models import User

pytestmark = [pytest.mark.e2e]

_QUESTION_ARGS = {
    "questions": [
        {
            "question": "Which  region should\nthe report cover?",
            "header": "region",
            "options": [{"label": "EMEA"}, {"label": "APAC"}],
        }
    ]
}
_APPROVAL_ARGS = {
    "title": "Delete the draft invoices",
    "reason": "They were superseded.",
    "tool_name": "pod_delete_record",
    "args": {"table": "invoices"},
}
_PERMISSION_ARGS = {
    "title": "Run `npm test`",
    "reason": None,
    "tool_name": "exec_command",
    AGENT_HOST_PERMISSION_KEY: {
        "request_id": "perm-1",
        "input": {"cmd": "npm test"},
        "options": [{"option_id": "allow_once", "kind": "allowonce", "name": "Allow"}],
    },
}


@pytest.fixture
async def pod_id(authenticated_client, fixed_test_org) -> str:
    pod = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Waiting On You {uuid4().hex[:8]}",
            "description": "pending interactions e2e",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod.status_code == 201, pod.text
    identifier = pod.json()["id"]
    agent = await authenticated_client.post(
        f"/pods/{identifier}/agents",
        json={
            "name": "Ledger Agent",
            "instruction": "Answer in plain text.",
            "agent_runtime": {"profile_id": "system:lemma"},
        },
    )
    assert agent.status_code == 201, agent.text
    return identifier


async def _conversation(client, pod_id: str, title: str) -> UUID:
    created = await client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "ledger_agent", "title": title, "type": "CHAT"},
    )
    assert created.status_code == 201, created.text
    return UUID(created.json()["id"])


async def _pause(
    conversation_id: UUID,
    *,
    tool_name: str,
    tool_args: dict,
    status: ConversationStatus,
    asked_ago: timedelta = timedelta(0),
) -> str:
    """A run that called ``tool_name`` and left the conversation at ``status``."""
    tool_call_id = f"call-{uuid4().hex[:12]}"
    async with create_uow_from_session_maker(async_session_maker) as uow:
        messages = Messages(uow)
        runs = Runs(uow)
        run = await runs.create_agent_run(
            conversation_id=conversation_id,
            agent_id=None,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
        )
        message = await messages.append_message(
            conversation_id=conversation_id,
            agent_run_id=run.id,
            draft=MessageDraft.of_tool_call(
                tool_call_id=tool_call_id,
                tool_name=tool_name,
                tool_args=tool_args,
                pause=opening_for(tool_name, tool_args),
            ),
        )
        # Scenery written straight in: how the run got here is not the subject.
        await uow.session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == conversation_id)
            .values(status=status.value)
        )
        if asked_ago:
            await uow.session.execute(
                update(MessageModel)
                .where(MessageModel.id == message.id)
                .values(created_at=MessageModel.created_at - asked_ago)
            )
            await uow.session.execute(
                update(AgentPauseModel)
                .where(
                    AgentPauseModel.conversation_id == conversation_id,
                    AgentPauseModel.tool_call_id == tool_call_id,
                )
                .values(created_at=AgentPauseModel.created_at - asked_ago)
            )
        await uow.commit()
    return tool_call_id


async def _decide(conversation_id: UUID, tool_call_id: str, user_id: str) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        pauses = Pauses(uow)
        pause = await pauses.get(
            conversation_id=conversation_id, tool_call_id=tool_call_id
        )
        assert pause is not None
        assert pause.supersede(by=UUID(user_id))
        assert await pauses.save(pause)
        await uow.commit()


async def _waiting(client, pod_id: str, **params) -> dict:
    listed = await client.get(
        f"/pods/{pod_id}/conversations/pending-interactions", params=params
    )
    assert listed.status_code == 200, listed.text
    return listed.json()


async def test_questions_approvals_and_permissions_are_listed_longest_first(
    authenticated_client, pod_id
) -> None:
    asked = await _conversation(authenticated_client, pod_id, "Quarterly report")
    approve = await _conversation(authenticated_client, pod_id, "Invoice cleanup")
    host = await _conversation(authenticated_client, pod_id, "Local build")
    question_id = await _pause(
        asked,
        tool_name="ask_user",
        tool_args=_QUESTION_ARGS,
        status=ConversationStatus.WAITING,
        asked_ago=timedelta(hours=3),
    )
    approval_id = await _pause(
        approve,
        tool_name="request_approval",
        tool_args=_APPROVAL_ARGS,
        status=ConversationStatus.WAITING,
        asked_ago=timedelta(hours=1),
    )
    permission_id = await _pause(
        host,
        tool_name="request_approval",
        tool_args=_PERMISSION_ARGS,
        status=ConversationStatus.RUNNING,
    )

    items = (await _waiting(authenticated_client, pod_id))["items"]

    assert [(i["tool_call_id"], i["kind"]) for i in items] == [
        (question_id, "QUESTION"),
        (approval_id, "APPROVAL"),
        (permission_id, "PERMISSION"),
    ]
    assert items[0]["summary"] == "Which region should the report cover?"
    assert items[0]["conversation_title"] == "Quarterly report"
    assert items[0]["agent_name"] == "ledger_agent"
    assert items[1]["summary"] == "Delete the draft invoices"
    assert items[2]["conversation_status"] == "RUNNING"


async def test_only_pauses_still_waiting_on_this_person_are_listed(
    authenticated_client, pod_id, fixed_test_user
) -> None:
    """Decided, answered, dismissed, overtaken, timed out by the host, or not theirs."""
    answered = await _conversation(authenticated_client, pod_id, "Answered")
    answered_call = await _pause(
        answered,
        tool_name="ask_user",
        tool_args=_QUESTION_ARGS,
        status=ConversationStatus.WAITING,
    )
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await Messages(uow).append_message(
            conversation_id=answered,
            agent_run_id=None,
            draft=MessageDraft.of_tool_return(
                tool_call_id=answered_call,
                tool_name="ask_user",
                tool_result={"answers": {"region": "EMEA"}},
            ),
        )
        await uow.commit()
    dismissed = await _conversation(authenticated_client, pod_id, "Dismissed")
    await _pause(
        dismissed,
        tool_name="ask_user",
        tool_args=_QUESTION_ARGS,
        status=ConversationStatus.WAITING,
    )
    archived = await authenticated_client.patch(
        f"/pods/{pod_id}/conversations/{dismissed}", json={"is_archived": True}
    )
    assert archived.status_code == 200, archived.text
    decided = await _conversation(authenticated_client, pod_id, "Decided")
    await _decide(
        decided,
        await _pause(
            decided,
            tool_name="request_approval",
            tool_args=_APPROVAL_ARGS,
            status=ConversationStatus.WAITING,
        ),
        fixed_test_user["id"],
    )
    overtaken = await _conversation(authenticated_client, pod_id, "Overtaken")
    await _pause(
        overtaken,
        tool_name="ask_user",
        tool_args=_QUESTION_ARGS,
        status=ConversationStatus.FAILED,
    )
    expired = await _conversation(authenticated_client, pod_id, "Host gave up")
    await _pause(
        expired,
        tool_name="request_approval",
        tool_args=_PERMISSION_ARGS,
        status=ConversationStatus.RUNNING,
        asked_ago=timedelta(minutes=31),
    )
    theirs = await _conversation(authenticated_client, pod_id, "Somebody else's")
    await _pause(
        theirs,
        tool_name="ask_user",
        tool_args=_QUESTION_ARGS,
        status=ConversationStatus.WAITING,
    )
    async with create_uow_from_session_maker(async_session_maker) as uow:
        stranger = User(email=f"stranger-{uuid4().hex[:8]}@example.com")
        uow.session.add(stranger)
        await uow.session.flush()
        await uow.session.execute(
            update(ConversationModel)
            .where(ConversationModel.id == theirs)
            .values(user_id=stranger.id)
        )
        await uow.commit()

    assert (await _waiting(authenticated_client, pod_id))["items"] == []


async def test_the_list_pages_without_skipping_or_repeating(
    authenticated_client, pod_id
) -> None:
    asked = [
        await _pause(
            await _conversation(authenticated_client, pod_id, f"Ask {index}"),
            tool_name="ask_user",
            tool_args=_QUESTION_ARGS,
            status=ConversationStatus.WAITING,
            asked_ago=timedelta(minutes=10 - index),
        )
        for index in range(3)
    ]

    first = await _waiting(authenticated_client, pod_id, limit=2)
    second = await _waiting(
        authenticated_client, pod_id, limit=2, page_token=first["next_page_token"]
    )

    assert [i["tool_call_id"] for i in first["items"] + second["items"]] == asked
    assert second["next_page_token"] is None


async def test_a_conversation_says_whether_it_is_waiting_on_you(
    authenticated_client, pod_id
) -> None:
    """On the history list and on the conversation itself, from the same rule."""
    waiting = await _conversation(authenticated_client, pod_id, "Waiting on you")
    await _pause(
        waiting,
        tool_name="request_approval",
        tool_args=_PERMISSION_ARGS,
        status=ConversationStatus.RUNNING,
    )
    quiet = await _conversation(authenticated_client, pod_id, "Just running")
    await _pause(
        quiet,
        tool_name="ask_user",
        tool_args=_QUESTION_ARGS,
        status=ConversationStatus.COMPLETED,
    )

    listed = await authenticated_client.get(f"/pods/{pod_id}/conversations")
    assert listed.status_code == 200, listed.text
    flags = {
        UUID(item["id"]): item["has_pending_interaction"]
        for item in listed.json()["items"]
    }
    assert flags[waiting] is True
    assert flags[quiet] is False

    one = await authenticated_client.get(f"/pods/{pod_id}/conversations/{waiting}")
    assert one.status_code == 200, one.text
    assert one.json()["has_pending_interaction"] is True


async def test_a_malformed_page_token_is_refused(authenticated_client, pod_id) -> None:
    refused = await authenticated_client.get(
        f"/pods/{pod_id}/conversations/pending-interactions",
        params={"page_token": "not-a-token"},
    )
    assert refused.status_code == 400, refused.text
