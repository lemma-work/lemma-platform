"""Claiming, withdrawing and releasing queued messages, against real PostgreSQL.

A message sent mid-run can be taken back until something is carrying it. Each
transition is one ``UPDATE``/``DELETE`` whose ``WHERE`` restates the state it
leaves, so this is the race that matters: the person withdrawing a message
while an Agent Host run is being dispatched with it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.infrastructure.harnesses.agent_host.dispatch import (
    CarriedChanged,
    claim_exactly,
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


async def test_a_message_withdrawn_while_the_prompt_was_built_rolls_the_admission_back(
    db_session, scenario
):
    """The claim and the run's admission are one transaction, and it is short.

    The prompt was built carrying both queued messages; one was withdrawn
    before the admission. The claim comes back short, so nothing is claimed --
    the dispatch rebuilds without it -- and no crash between two commits can
    ever leave a message claimed by a run that never went out, because there
    are no longer two commits.
    """
    await scenario.create_org_with_pod(name_prefix="Queued claims")
    conversation_id, run_id, rows = await _seed(db_session, scenario)
    kept, withdrawn = rows[1].id, rows[2].id
    uow = SqlAlchemyUnitOfWork(db_session)
    queued = QueuedMessageRepository(uow)
    assert await queued.withdraw_queued_user_message(
        conversation_id=conversation_id, message_id=withdrawn
    )
    await db_session.commit()

    with pytest.raises(CarriedChanged) as changed:
        await claim_exactly(
            uow, agent_run_id=run_id, carried=frozenset({kept, withdrawn})
        )
    await uow.rollback()

    assert changed.value.lost == {withdrawn}
    # Rolled back with the admission: still queued, so still the person's.
    assert await queued.withdraw_queued_user_message(
        conversation_id=conversation_id, message_id=kept
    )


async def test_a_claim_that_matches_the_prompt_holds_until_admission_commits(
    db_session, scenario
):
    await scenario.create_org_with_pod(name_prefix="Queued admission")
    conversation_id, run_id, rows = await _seed(db_session, scenario)
    uow = SqlAlchemyUnitOfWork(db_session)

    await claim_exactly(
        uow, agent_run_id=run_id, carried=frozenset({rows[1].id, rows[2].id})
    )
    await uow.commit()

    # Claimed, so on its way: no longer the person's to take back.
    queued = QueuedMessageRepository(uow)
    for row in rows[1:]:
        assert not await queued.withdraw_queued_user_message(
            conversation_id=conversation_id, message_id=row.id
        )


async def _seed_mixed(db_session, scenario, *, private_run: bool):
    """A running turn with a private note and a message for the platform queued."""
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
        run_metadata={"private_note": True} if private_run else {"source": "x"},
        started_at=_BASE,
        created_at=_BASE,
    )
    db_session.add(run)
    note, platform = (
        MessageModel(
            id=uuid4(),
            conversation_id=conversation.id,
            agent_run_id=run.id,
            sequence=index,
            role="user",
            kind="TEXT",
            text=text,
            message_metadata=metadata,
            created_at=_BASE + timedelta(seconds=index),
        )
        for index, (text, metadata) in enumerate(
            [
                (
                    "Don't promise Friday.",
                    {"during_active_run": True, "private_note": True},
                ),
                ("@lemmabot and the PO number?", {"during_active_run": True}),
            ],
            start=1,
        )
    )
    db_session.add_all([note, platform])
    await db_session.commit()
    return run.id, note.id, platform.id


@pytest.mark.parametrize("private_run", [False, True])
async def test_a_running_answer_takes_in_only_messages_of_its_own_kind(
    db_session, scenario, private_run
):
    """A note never shapes an answer the group gets, nor the other way round."""
    from app.modules.agent.infrastructure.repositories import ConversationRepository

    await scenario.create_org_with_pod(name_prefix="Queued kinds")
    run_id, note, platform = await _seed_mixed(
        db_session, scenario, private_run=private_run
    )
    repository = ConversationRepository(SqlAlchemyUnitOfWork(db_session))

    steered = await repository.claim_queued_user_messages(run_id, as_run=True)
    await db_session.commit()

    assert [message.id for message in steered] == [note if private_run else platform]
    # The other one waits for a follow-up of its own kind, and the oldest
    # message waiting decides that kind.
    assert await repository.count_queued_user_messages(run_id) == 1
    assert await repository.earliest_queued_is_note(run_id) is (not private_run)
