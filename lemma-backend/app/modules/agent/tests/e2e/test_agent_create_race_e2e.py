"""Two creates of one agent name at once: one wins, the other gets the 409.

The service looks the name up before inserting, and two creates racing each
other both pass that check. The unique constraint then decides -- and the
loser's ``IntegrityError`` reached the client as a bare 500, where the same
duplicate sent a moment later gets ``AGENT_ALREADY_EXISTS``.
"""

from __future__ import annotations

import asyncio
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from starlette import status

from app.core.authorization.context import ResourceVisibility
from app.core.infrastructure.db.session import async_session_maker
from app.modules.agent.domain.agent_kind import AgentKind
from app.modules.agent.infrastructure.models import AgentModel
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e]

_INSTRUCTION = "Return a concise answer."


async def _blocked_behind_the_competitor() -> None:
    """Until the request waits on the competitor's uncommitted transaction.

    Matched on the wait rather than the statement text: a session's ``query``
    column can still show its previous statement while the next one waits.

    The statistics snapshot is cleared on every read. ``pg_stat_activity`` is
    fixed for the length of a transaction once first read, and the probe reads
    it repeatedly in one -- so a first read made before the request connected
    never saw it arrive, and the wait timed out with the request already parked
    on the INSERT.
    """
    async with async_session_maker() as session:

        async def waiting_on_a_transaction() -> int:
            await session.execute(text("SELECT pg_stat_clear_snapshot()"))
            return (
                await session.scalar(
                    text(
                        "SELECT count(*) FROM pg_stat_activity "
                        "WHERE wait_event_type = 'Lock' "
                        "AND wait_event = 'transactionid' "
                        "AND datname = current_database() "
                        "AND pid <> pg_backend_pid()"
                    )
                )
                or 0
            )

        await eventually(
            label="the create waiting on the competing insert",
            probe=waiting_on_a_transaction,
            done=lambda waiting: waiting > 0,
            timeout_seconds=10.0,
            interval_seconds=0.05,
        )


async def test_the_create_that_loses_the_race_gets_the_same_409(
    scenario: E2EScenario,
) -> None:
    """Deterministic: the competing row is inserted and held uncommitted, so
    the request's name check cannot see it and its INSERT has to wait for it."""
    await scenario.create_org_with_pod(name_prefix="Agent race")
    name = f"racing-agent-{uuid4().hex[:8]}"

    async with async_session_maker() as competitor:
        competitor.add(
            AgentModel(
                pod_id=UUID(scenario.pod_id),
                user_id=UUID(scenario.owner_user["id"]),
                name=name,
                kind=AgentKind.USER.value,
                visibility=ResourceVisibility.PERSONAL.value,
                instruction=_INSTRUCTION,
                toolsets=[],
            )
        )
        await competitor.flush()
        create = asyncio.create_task(
            scenario.owner_client.post(
                f"/pods/{scenario.pod_id}/agents",
                json={"name": name, "instruction": _INSTRUCTION},
            )
        )
        await _blocked_behind_the_competitor()
        await competitor.commit()
        response = await create

    assert response.status_code == status.HTTP_409_CONFLICT, response.text
    assert response.json()["code"] == "AGENT_ALREADY_EXISTS"


async def test_two_creates_at_once_give_one_agent_and_one_409(
    scenario: E2EScenario,
) -> None:
    await scenario.create_org_with_pod(name_prefix="Agent race")
    name = f"racing-agent-{uuid4().hex[:8]}"

    responses = await asyncio.gather(
        *(
            scenario.owner_client.post(
                f"/pods/{scenario.pod_id}/agents",
                json={"name": name, "instruction": _INSTRUCTION},
            )
            for _ in range(2)
        )
    )

    assert sorted(r.status_code for r in responses) == [
        status.HTTP_201_CREATED,
        status.HTTP_409_CONFLICT,
    ], [r.text for r in responses]
    (refused,) = [r for r in responses if r.status_code == status.HTTP_409_CONFLICT]
    assert refused.json()["code"] == "AGENT_ALREADY_EXISTS"
    assert refused.json()["request_id"]
