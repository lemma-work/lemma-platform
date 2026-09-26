"""Claiming, withdrawing and releasing queued messages, against real PostgreSQL.

A message sent mid-run can be taken back until something is carrying it. Each
transition is one ``UPDATE``/``DELETE`` whose ``WHERE`` restates the state it
leaves, so these are the races that matter: the person withdrawing a message
while an Agent Host run is being dispatched with it, and a dispatch that fails
after claiming.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.harnesses.agent_host.dispatch import (
    claim_carried,
)
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.infrastructure.queued_message_queries import (
    QueuedMessageRepository,
)

pytestmark = [pytest.mark.e2e]

_BASE = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


async def _seed(db_session, scenario) -> tuple[UUID, UUID, list[MessageModel]]:
    """A running turn: the message that started it, and two sent since."""
    conversation = ConversationModel(
        id=uuid4(),
        user_id=UUID(scenario.owner_user["id"]),
        pod_id=UUID(scenario.pod_id),
        organization_id=UUID(scenario.org_id),
    )
    db_session.add(conversation)
    run = AgentRunModel(
        id=uuid4(),
        conversation_id=conversation.id,
        status="RUNNING",
        agent_runtime={"profile_id": "system:lemma"},
        started_at=_BASE,
        created_at=_BASE,
    )
    db_session.add(run)
    rows = [
        MessageModel(
            id=uuid4(),
            conversation_id=conversation.id,
            agent_run_id=run.id,
            sequence=index,
            role="user",
            kind="TEXT",
            text=text,
            message_metadata={"during_active_run": index > 0},
            created_at=_BASE + timedelta(seconds=index),
        )
        for index, text in enumerate(
            ["Refactor the parser.", "Keep the old API.", "Never mind that."]
        )
    ]
    db_session.add_all(rows)
    await db_session.commit()
    return conversation.id, run.id, rows


async def test_a_message_withdrawn_while_the_run_is_dispatched_is_not_sent(
    db_session, scenario
):
    await scenario.create_org_with_pod(name_prefix="Queued claims")
    conversation_id, run_id, rows = await _seed(db_session, scenario)
    loaded = [row.to_entity() for row in rows]
    uow = SqlAlchemyUnitOfWork(db_session)
    queued = QueuedMessageRepository(uow)

    # Taken back after the runner loaded the history, before dispatch claimed.
    assert await queued.withdraw_queued_user_message(
        conversation_id=conversation_id, message_id=rows[2].id
    )
    await db_session.commit()

    carried = await claim_carried(uow, loaded, agent_run_id=run_id)

    assert [message.text for message in carried.messages] == [
        "Refactor the parser.",
        "Keep the old API.",
    ]
    assert carried.claimed == {rows[1].id}
    # Claimed, so on its way: no longer the person's to take back.
    assert not await queued.withdraw_queued_user_message(
        conversation_id=conversation_id, message_id=rows[1].id
    )


async def test_a_dispatch_that_fails_hands_its_claim_back(db_session, scenario):
    await scenario.create_org_with_pod(name_prefix="Queued release")
    conversation_id, run_id, rows = await _seed(db_session, scenario)
    uow = SqlAlchemyUnitOfWork(db_session)
    queued = QueuedMessageRepository(uow)
    carried = await claim_carried(
        uow, [row.to_entity() for row in rows], agent_run_id=run_id
    )

    await queued.release_claims(run_id, message_ids=sorted(carried.claimed))
    await db_session.commit()

    # Queued again: withdrawable, and owed to the follow-up turn.
    assert await queued.withdraw_queued_user_message(
        conversation_id=conversation_id, message_id=rows[1].id
    )
