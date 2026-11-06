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
from app.modules.agent.agent_host.harness.dispatch import (
    CarriedChanged,
    claim_exactly,
)
from app.modules.agent.infrastructure.models import (
    AgentRunModel,
    ConversationModel,
    MessageModel,
)
from app.modules.agent.infrastructure.db.queued_messages import QueuedMessages
from app.modules.agent.domain.run_places import (
    ASKED_IN_PLACE_KEY,
    IN_LEMMA,
    AnswerScope,
    in_lemma,
)
from app.modules.agent.domain.run_state import RunState
from app.modules.agent.domain.value_objects import QueueState

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
            queue_state=QueueState.QUEUED if index > 0 else QueueState.NONE,
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
    queued = QueuedMessages(uow)
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
    queued = QueuedMessages(uow)
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
        private_note=private_run,
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
            queue_state=QueueState.QUEUED,
            private_note=private_note,
            created_at=_BASE + timedelta(seconds=index),
        )
        for index, (text, private_note) in enumerate(
            [
                ("Don't promise Friday.", True),
                ("@lemmabot and the PO number?", False),
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
    from app.modules.agent.infrastructure.db.queued_messages import QueuedMessages

    await scenario.create_org_with_pod(name_prefix="Queued kinds")
    run_id, note, platform = await _seed_mixed(
        db_session, scenario, private_run=private_run
    )
    queued_messages = QueuedMessages(SqlAlchemyUnitOfWork(db_session))

    steered = await queued_messages.claim_queued_user_messages(run_id, as_run=True)
    await db_session.commit()

    assert [message.id for message in steered] == [note if private_run else platform]
    # The other one waits for a follow-up of its own kind, and the oldest
    # message waiting decides that kind.
    assert await queued_messages.count_queued_user_messages(run_id) == 1
    assert await queued_messages.earliest_queued_scope(run_id) == AnswerScope(
        notes=not private_run, place=None
    )


_EMAIL, _WHATSAPP = uuid4(), uuid4()


async def _seed_places(db_session, scenario, *, asked_at=None):
    """A running turn asked at one place, with messages queued from four: the
    email it was asked by, WhatsApp, Lemma's composer (no place), and one of
    Lemma's own channels -- each place stored the way the server writes it."""
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
        run_metadata={ASKED_IN_PLACE_KEY: str(asked_at)} if asked_at else None,
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
            queue_state=QueueState.QUEUED,
            message_metadata=metadata,
            created_at=_BASE + timedelta(seconds=index),
        )
        for index, (text, metadata) in enumerate(
            [
                ("And the second invoice?", {ASKED_IN_PLACE_KEY: str(_EMAIL)}),
                ("Are you there?", {ASKED_IN_PLACE_KEY: str(_WHATSAPP)}),
                ("Typed in Lemma.", None),
                (
                    "From a channel in Lemma.",
                    {"source": "lemma_channel", ASKED_IN_PLACE_KEY: IN_LEMMA},
                ),
            ],
            start=1,
        )
    ]
    db_session.add_all(rows)
    await db_session.commit()
    return run.id, [row.id for row in rows]


async def test_a_running_answer_takes_in_only_what_was_said_where_it_was_asked(
    db_session, scenario
):
    """A run asked by email steers in the email that followed, and what was
    typed in Lemma's composer, which was said nowhere in particular -- not the
    WhatsApp message, nor the line from Lemma's own channels. Those wait for
    turns of their own, answered where they were said."""
    await scenario.create_org_with_pod(name_prefix="Queued places")
    run_id, (email, whatsapp, composer, in_lemma) = await _seed_places(
        db_session, scenario, asked_at=_EMAIL
    )
    queued_messages = QueuedMessages(SqlAlchemyUnitOfWork(db_session))

    steered = await queued_messages.claim_queued_user_messages(run_id, as_run=True)
    await db_session.commit()

    assert [message.id for message in steered] == [email, composer]
    assert await queued_messages.count_queued_user_messages(run_id) == 2
    # The oldest waiting decides the follow-up's scope: WhatsApp, which takes
    # only its own, leaving the line from Lemma for the next.
    whatsapp_scope = AnswerScope(notes=False, place=str(_WHATSAPP))
    assert await queued_messages.earliest_queued_scope(run_id) == whatsapp_scope
    assert (
        await queued_messages.count_queued_user_messages(run_id, beside=whatsapp_scope)
        == 1
    )
    claimed = await queued_messages.claim_queued_user_messages(
        run_id, scope=whatsapp_scope
    )
    assert [message.id for message in claimed] == [whatsapp]
    assert await queued_messages.earliest_queued_scope(run_id) == AnswerScope(
        notes=False, place=IN_LEMMA
    )
    assert in_lemma not in {message.id for message in [*steered, *claimed]}


async def test_a_run_asked_nowhere_in_particular_takes_in_only_what_was_too(
    db_session, scenario
):
    """Typed in Lemma's composer: the WhatsApp and email messages that came
    meanwhile are each answered at their own place, not by this run."""
    await scenario.create_org_with_pod(name_prefix="Queued nowhere")
    run_id, (_email, _whatsapp, composer, _in_lemma) = await _seed_places(
        db_session, scenario
    )
    queued_messages = QueuedMessages(SqlAlchemyUnitOfWork(db_session))

    steered = await queued_messages.claim_queued_user_messages(run_id, as_run=True)

    assert [message.id for message in steered] == [composer]


async def test_a_group_thread_is_steered_by_the_place_stored_with_it(
    db_session, scenario
):
    """A run asked in one thread of a group takes in a reply in that thread,
    not a line in another: both compared as the place stored with them, the
    name ``in_lemma`` gave it once -- the database never builds one."""
    await scenario.create_org_with_pod(name_prefix="Queued threads")
    asked, other = uuid4(), uuid4()
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
        **AgentRunModel.state_values(
            RunState(asked_in_lemma=True, asked_in_thread=asked)
        ),
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
            queue_state=QueueState.QUEUED,
            message_metadata={
                "source": "lemma_channel",
                ASKED_IN_PLACE_KEY: in_lemma(thread),
            },
            created_at=_BASE + timedelta(seconds=index),
        )
        for index, (text, thread) in enumerate(
            [("In the other thread.", other), ("In the same thread.", asked)],
            start=1,
        )
    ]
    db_session.add_all(rows)
    await db_session.commit()
    queued_messages = QueuedMessages(SqlAlchemyUnitOfWork(db_session))

    steered = await queued_messages.claim_queued_user_messages(run.id, as_run=True)
    await db_session.commit()

    assert [message.id for message in steered] == [rows[1].id]
    # What waits is read back as stored, and is the scope the code would
    # name for a run asked in that thread.
    other_scope = AnswerScope.of_run(
        RunState(asked_in_lemma=True, asked_in_thread=other).privacy
    )
    assert await queued_messages.earliest_queued_scope(run.id) == other_scope
