"""Asking a pod's agent from an MCP host, against a real pod.

The ask opens a conversation of the person's own, marked as the connection's,
with the request as their message; the check reads the run's answer back, for
that connection only. No worker and no model: runs are started with the
enqueue suppressed and finished by hand, which is all the reads look at.
"""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from fastapi import status

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.services.pod_mcp_agent_tools import (
    AgentAnswerArguments,
    AgentAskRefused,
    AskAgentArguments,
)
from app.modules.agent.services.pod_mcp_agents import (
    CONNECTION_KEY,
    INSTRUCTIONS,
    MCP_SOURCE,
    AnswerStatus,
    PodAgentAsks,
)
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from app.modules.agent.tests.e2e.test_agent_e2e import _create_test_pod
from app.modules.agent.tests.e2e.test_pod_asks_e2e import _finish_with, _messages
from app.modules.mcp_access.contracts import McpPrincipal, Scope

pytestmark = pytest.mark.e2e


def _connection(*, user_id: UUID, pod_id: UUID) -> McpPrincipal:
    return McpPrincipal(
        user_id=user_id,
        pod_id=pod_id,
        grant_id=uuid4(),
        client_id="client",
        client_name="ChatGPT",
        scopes=frozenset({Scope.READ, Scope.WRITE}),
    )


@pytest.mark.asyncio
async def test_an_ask_opens_the_persons_own_conversation_and_reads_its_answer(
    authenticated_client, fixed_test_org, fixed_test_user
):
    pod = await _create_test_pod(authenticated_client, fixed_test_org)
    person = UUID(fixed_test_user["id"])
    connection = _connection(user_id=person, pod_id=UUID(pod))
    asks = PodAgentAsks(SessionUnitOfWorkFactory(async_session_maker))

    agents = await asks.agents(pod_id=UUID(pod), user_id=person)
    assert agents and agents[0].is_assistant

    with suppress_agent_run_enqueue():
        working = await asks.ask(
            AskAgentArguments(message="How many sign-ups yesterday?", wait_seconds=0),
            principal=connection,
        )
    assert working.status is AnswerStatus.WORKING

    # The person's own conversation, in their chats, saying which connection
    # opened it and telling the agent where its answer goes.
    thread = await authenticated_client.get(
        f"/pods/{pod}/conversations/{working.conversation_id}"
    )
    assert thread.status_code == status.HTTP_200_OK, thread.text
    body = thread.json()
    assert body["user_id"] == str(person)
    assert body["instructions"] == INSTRUCTIONS
    assert body["metadata"][CONNECTION_KEY]["grant_id"] == str(connection.grant_id)
    requests = [m for m in await _messages(working.conversation_id) if m.role == "user"]
    assert [m.text for m in requests] == ["How many sign-ups yesterday?"]
    assert requests[0].metadata["source"] == MCP_SOURCE

    # Another connection of the same person did not ask it, so cannot read it.
    with pytest.raises(AgentAskRefused, match="No conversation this connection"):
        await asks.check(
            AgentAnswerArguments(conversation_id=working.conversation_id),
            principal=_connection(user_id=person, pod_id=UUID(pod)),
        )

    await _finish_with(working.conversation_id, words="Forty-two.")
    answered = await asks.check(
        AgentAnswerArguments(conversation_id=working.conversation_id, wait_seconds=0),
        principal=connection,
    )
    assert answered.status is AnswerStatus.ANSWERED
    assert answered.answer == "Forty-two."

    # A follow-up continues the same conversation rather than opening another.
    with suppress_agent_run_enqueue():
        followed = await asks.ask(
            AskAgentArguments(
                message="And the day before?",
                conversation_id=working.conversation_id,
                wait_seconds=0,
            ),
            principal=connection,
        )
    assert followed.conversation_id == working.conversation_id
    assert followed.status is AnswerStatus.WORKING
