"""`list_runs_stuck_stopping` against a real database.

The settling logic around this query is unit-tested against a fake repository,
and a fake is exactly what cannot answer the question that matters here: whether
the SQL selects the right rows. The two things it decides — that only
STOP_REQUESTED is eligible, and that eligibility is measured from when the stop
was written rather than when the run started — are both invisible to a stand-in
that simply hands back a list.

Getting either wrong is expensive in opposite directions. Too broad and the
sweep finishes runs that are still working; too narrow and a conversation stays
wedged, refusing new messages, which is the defect this query exists to end.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import update

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    AgentRuntimeConfig,
)
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.repositories import ConversationRepository

pytestmark = [pytest.mark.e2e]


@pytest.fixture
async def conversation_for_query(authenticated_client, fixed_test_org) -> UUID:
    """One conversation the runs in this module hang off. Its own pod, so these
    runs never collide with another test's active-run slot."""
    pod = await authenticated_client.post(
        "/pods",
        json={
            "name": f"Stuck Stop Pod {uuid4().hex[:8]}",
            "description": "stuck-stop query e2e",
            "organization_id": fixed_test_org["id"],
        },
    )
    assert pod.status_code == 201, pod.text
    pod_id = pod.json()["id"]

    agent = await authenticated_client.post(
        f"/pods/{pod_id}/agents",
        json={
            "name": "Query Agent",
            "instruction": "Answer in plain text.",
            "agent_runtime": {"profile_id": "system:lemma"},
        },
    )
    assert agent.status_code == 201, agent.text

    conversation = await authenticated_client.post(
        f"/pods/{pod_id}/conversations",
        json={"agent_name": "query_agent", "title": "Stuck", "type": "CHAT"},
    )
    assert conversation.status_code == 201, conversation.text
    return UUID(conversation.json()["id"])


async def _run_in(conversation_id: UUID, *, status: AgentRunStatus, age: timedelta):
    """A run of the given status, last touched `age` ago."""
    async with create_uow_from_session_maker(async_session_maker) as uow:
        run = await ConversationRepository(uow).create_agent_run(
            conversation_id=conversation_id,
            agent_id=None,
            agent_runtime=AgentRuntimeConfig(profile_id="system:lemma"),
            metadata={"source": "e2e_stuck_stop_query"},
        )
        await uow.session.flush()
        await uow.session.execute(
            update(AgentRunModel)
            .where(AgentRunModel.id == run.id)
            .values(
                status=status.value,
                updated_at=datetime.now(timezone.utc) - age,
                # Deliberately recent, so a query keyed on the wrong column
                # cannot pass by accident.
                started_at=datetime.now(timezone.utc),
            )
        )
        await uow.commit()
        return run.id


async def _stuck(cutoff_seconds: int) -> set[UUID]:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        rows = await ConversationRepository(uow).list_runs_stuck_stopping(
            cutoff_seconds=cutoff_seconds
        )
        return {row.id for row in rows}


async def test_it_finds_a_stop_older_than_the_cutoff(conversation_for_query) -> None:
    stuck = await _run_in(
        conversation_for_query,
        status=AgentRunStatus.STOP_REQUESTED,
        age=timedelta(seconds=600),
    )

    assert stuck in await _stuck(120)


async def test_a_recent_stop_is_left_alone(conversation_for_query) -> None:
    """A worker that has had the stop for ten seconds is still acting on it."""
    fresh = await _run_in(
        conversation_for_query,
        status=AgentRunStatus.STOP_REQUESTED,
        age=timedelta(seconds=10),
    )

    assert fresh not in await _stuck(120)


async def test_a_running_run_is_never_stuck_stopping(conversation_for_query) -> None:
    """The status filter. A long RUNNING run is the sweep's worst mistake: it is
    working, and finishing it destroys the work."""
    running = await _run_in(
        conversation_for_query,
        status=AgentRunStatus.RUNNING,
        age=timedelta(seconds=6000),
    )

    assert running not in await _stuck(120)


async def test_a_finished_run_is_never_stuck_stopping(conversation_for_query) -> None:
    done = await _run_in(
        conversation_for_query,
        status=AgentRunStatus.STOPPED,
        age=timedelta(seconds=6000),
    )

    assert done not in await _stuck(120)
