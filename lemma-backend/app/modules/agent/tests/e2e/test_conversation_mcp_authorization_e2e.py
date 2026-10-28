"""Which relayed run tokens may use a conversation's tools.

Against real SuperTokens tokens and real rows, because every rule here is a
question about one or the other: what a token was minted for, and whether the
agent, run and lease it names still say so. See ``conversation_mcp_authorization``.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid7

import pytest
from sqlalchemy import delete, update

from app.modules.agent.infrastructure.models import AgentModel, AgentRunModel
from app.modules.agent.infrastructure.runtime_models import AgentHostRunLeaseModel
from app.modules.agent.agent_host.mcp_authorization import RelayingHost
from app.modules.agent.agent_host.mcp import ConversationMCPService
from app.modules.agent.tests.e2e.agent_host_helpers import paired_machine
from app.modules.identity.infrastructure.supertokens_auth.helpers import get_user_token
from app.modules.identity.infrastructure.supertokens_auth.token_factory import (
    build_delegation_claims,
)

pytestmark = pytest.mark.e2e


async def _agent_run(db_session, scenario) -> tuple[UUID, UUID, UUID]:
    """(agent, conversation, run): a named agent mid-run in its conversation."""
    agent = await scenario.create_agent()
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations",
        json={"title": "relayed tools", "agent_name": agent["name"]},
    )
    assert created.status_code in {200, 201}, created.text
    conversation_id = UUID(created.json()["id"])
    run = AgentRunModel(
        conversation_id=conversation_id,
        agent_id=UUID(agent["id"]),
        status="RUNNING",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.commit()
    return UUID(agent["id"]), conversation_id, run.id


async def _run_token(scenario, *, agent_id: UUID, session_id: str) -> str:
    """The token dispatch mints for a run: the agent, acting for its person."""
    claims = build_delegation_claims(
        workload_type="AGENT",
        workload_id=agent_id,
        workload_name="relayed",
        pod_id=UUID(scenario.pod_id),
        session_id=session_id,
        invoked_by_user_id=UUID(scenario.owner_user["id"]),
    )
    return await get_user_token(
        UUID(scenario.owner_user["id"]), delegation_claims=claims
    )


async def test_the_runs_own_token_is_authorized(db_session, scenario):
    """Dispatch mints the run's token with the conversation as its session --
    the key a person's "allow for this conversation" is recorded under -- so
    the token the host relays names the conversation, not the run."""
    await scenario.create_org_with_pod(name_prefix="Relayed tools")
    agent_id, conversation_id, run_id = await _agent_run(db_session, scenario)
    token = await _run_token(
        scenario, agent_id=agent_id, session_id=str(conversation_id)
    )

    assert await ConversationMCPService().authorize(
        conversation_id=conversation_id, token=token, agent_run_id=run_id
    )


async def test_a_token_minted_with_the_run_as_its_session_still_works(
    db_session, scenario
):
    """A run in flight across the deploy holds a token naming the run."""
    await scenario.create_org_with_pod(name_prefix="Relayed tools")
    agent_id, conversation_id, run_id = await _agent_run(db_session, scenario)
    token = await _run_token(scenario, agent_id=agent_id, session_id=str(run_id))

    assert await ConversationMCPService().authorize(
        conversation_id=conversation_id, token=token, agent_run_id=run_id
    )


async def test_a_token_minted_for_another_run_is_refused(db_session, scenario):
    await scenario.create_org_with_pod(name_prefix="Relayed tools")
    agent_id, conversation_id, run_id = await _agent_run(db_session, scenario)
    token = await _run_token(scenario, agent_id=agent_id, session_id=str(uuid7()))

    assert not await ConversationMCPService().authorize(
        conversation_id=conversation_id, token=token, agent_run_id=run_id
    )


async def test_a_run_that_has_ended_has_no_tools(db_session, scenario):
    """A cancelled or failed run's agent kept calling tools until its token
    expired. A parked interaction's wait is the exception: it answers an
    ended run with its own refusal rather than an authorization failure."""
    await scenario.create_org_with_pod(name_prefix="Relayed tools")
    agent_id, conversation_id, run_id = await _agent_run(db_session, scenario)
    token = await _run_token(scenario, agent_id=agent_id, session_id=str(run_id))
    await db_session.execute(
        update(AgentRunModel)
        .where(AgentRunModel.id == run_id)
        .values(status="STOPPED", finished_at=datetime.now(timezone.utc))
    )
    await db_session.commit()

    service = ConversationMCPService()
    assert not await service.authorize(
        conversation_id=conversation_id, token=token, agent_run_id=run_id
    )
    assert await service.authorize(
        conversation_id=conversation_id,
        token=token,
        agent_run_id=run_id,
        allow_ended_run=True,
    )


async def test_deleting_the_agent_ends_its_runs_tools(db_session, scenario):
    """Through the API: deleting revokes the agent's tokens."""
    await scenario.create_org_with_pod(name_prefix="Relayed tools")
    agent_id, conversation_id, run_id = await _agent_run(db_session, scenario)
    token = await _run_token(scenario, agent_id=agent_id, session_id=str(run_id))
    agent = (await db_session.get(AgentModel, agent_id)).name
    deleted = await scenario.owner_client.delete(
        f"/pods/{scenario.pod_id}/agents/{agent}"
    )
    assert deleted.status_code in {200, 204}, deleted.text

    assert not await ConversationMCPService().authorize(
        conversation_id=conversation_id, token=token, agent_run_id=run_id
    )


async def test_a_deleted_agent_is_never_read_as_the_pods_assistant(
    db_session, scenario
):
    """The row gone and nothing revoked -- the revocation store fails open.

    The conversation's and the run's agent are both ON DELETE SET NULL, and a
    null agent reads as the pod's own assistant, which acts with the person's
    full standing. Without the row lookup every later tool call the deleted
    agent made was promoted to it.
    """
    await scenario.create_org_with_pod(name_prefix="Relayed tools")
    agent_id, conversation_id, run_id = await _agent_run(db_session, scenario)
    token = await _run_token(scenario, agent_id=agent_id, session_id=str(run_id))
    await db_session.execute(delete(AgentModel).where(AgentModel.id == agent_id))
    await db_session.commit()

    assert not await ConversationMCPService().authorize(
        conversation_id=conversation_id, token=token, agent_run_id=run_id
    )


async def test_a_token_relayed_by_another_persons_host_is_refused(db_session, scenario):
    await scenario.create_org_with_pod(name_prefix="Relayed tools")
    agent_id, conversation_id, run_id = await _agent_run(db_session, scenario)
    token = await _run_token(scenario, agent_id=agent_id, session_id=str(run_id))
    service = ConversationMCPService()

    assert not await service.authorize(
        conversation_id=conversation_id,
        token=token,
        agent_run_id=run_id,
        host=RelayingHost(host_id=uuid7(), user_id=uuid7()),
    )


async def test_a_run_answers_only_from_the_host_that_leases_it(db_session, scenario):
    await scenario.create_org_with_pod(name_prefix="Relayed tools")
    agent_id, conversation_id, run_id = await _agent_run(db_session, scenario)
    token = await _run_token(scenario, agent_id=agent_id, session_id=str(run_id))
    leasing = await paired_machine(scenario, display_name="leasing host")
    other = await paired_machine(scenario, display_name="other host")
    now = datetime.now(timezone.utc)
    db_session.add(
        AgentHostRunLeaseModel(
            run_id=run_id,
            host_id=leasing["host_id"],
            harness_id=leasing["harness_id"],
            lease_epoch=1,
            state="RUNNING",
            accepted_at=now,
            lease_expires_at=now + timedelta(minutes=5),
            created_at=now,
            updated_at=now,
        )
    )
    await db_session.commit()
    owner = UUID(scenario.owner_user["id"])
    service = ConversationMCPService()

    assert await service.authorize(
        conversation_id=conversation_id,
        token=token,
        agent_run_id=run_id,
        host=RelayingHost(host_id=leasing["host_id"], user_id=owner),
    )
    assert not await service.authorize(
        conversation_id=conversation_id,
        token=token,
        agent_run_id=run_id,
        host=RelayingHost(host_id=other["host_id"], user_id=owner),
    )
