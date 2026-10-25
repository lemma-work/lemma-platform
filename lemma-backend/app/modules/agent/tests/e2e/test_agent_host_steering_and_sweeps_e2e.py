"""Agent Host steering and the dispatch sweeps, against Postgres, without a host.

Steering and the two dispatch crons were driven only by the suites that build
and start the Rust host, which run in the Desktop lane and feed no coverage
floor. None of what they decide needs the binary: which queued messages go to
the host, what a host's answer about one does to it, which leases are dead and
which runs a machine must be told to stop, and what retention may delete. All of
that is SQL over lease, command and message rows, so it is seeded here as a
dispatched run leaves it and decided by the shipped code.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

import pytest
from sqlalchemy import select, update

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.modules.agent.domain.agent_host import (
    AgentHostCommandKind,
    AgentHostRunState,
)
from app.modules.agent.domain.queued_messages import (
    STEER_DISPATCHED_AT,
    STEER_UNDELIVERED,
    STEERED_INTO_RUN,
)
from app.modules.agent.domain.value_objects import (
    AgentRunStatus,
    MessageDraft,
    MessageRole,
)
from app.modules.agent.infrastructure.agent_host import recovery
from app.modules.agent.infrastructure.harnesses.agent_host.steering import (
    forward_queued_messages,
    record_steer_result,
)
from app.modules.agent.infrastructure.models import AgentRunModel, MessageModel
from app.modules.agent.infrastructure.db.messages import Messages
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostRunLeaseModel,
)
from app.modules.agent.tests.e2e.agent_host_helpers import (
    conversation_with_a_leased_run,
    paired_machine,
)
from app.modules.test_support.e2e.builders import E2EScenario

pytestmark = pytest.mark.e2e


async def _dispatched_run(
    db_session,
    scenario: E2EScenario,
    *,
    state: AgentHostRunState = AgentHostRunState.RUNNING,
    steering: bool = True,
) -> tuple[UUID, UUID, UUID]:
    """A conversation whose run a paired machine is working on."""
    if scenario.pod is None:
        await scenario.create_org_with_pod(name_prefix="Host sweeps")
    machine = await paired_machine(
        scenario,
        display_name="e2e sweeps",
        capabilities={"load_session": True, "steering": steering},
    )
    conversation_id, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=machine["host_id"],
        harness_id=machine["harness_id"],
        state=state,
        title="sweeps",
    )
    await db_session.commit()
    return conversation_id, run_id, machine["host_id"]


async def _sent_while_working(conversation_id: UUID, run_id: UUID, text: str) -> UUID:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        message = await Messages(uow).append_message(
            conversation_id=conversation_id,
            agent_run_id=run_id,
            draft=MessageDraft.of_text(
                text, role=MessageRole.USER, metadata={"during_active_run": True}
            ),
        )
        await uow.commit()
    return message.id


async def _metadata(message_id: UUID) -> dict[str, object]:
    async with async_session_maker() as session:
        message = await session.get(MessageModel, message_id)
        assert message is not None
        return dict(message.message_metadata or {})


async def _commands(
    run_id: UUID, kind: AgentHostCommandKind
) -> list[AgentHostCommandModel]:
    async with async_session_maker() as session:
        return list(
            (
                await session.execute(
                    select(AgentHostCommandModel).where(
                        AgentHostCommandModel.run_id == run_id,
                        AgentHostCommandModel.kind == kind.value,
                    )
                )
            ).scalars()
        )


async def _lease(run_id: UUID) -> AgentHostRunLeaseModel | None:
    async with async_session_maker() as session:
        return await session.get(AgentHostRunLeaseModel, run_id)


# ----------------------------------------------------------------- steering


async def test_a_message_sent_mid_turn_is_handed_to_the_host_once(
    db_session, scenario
) -> None:
    conversation_id, run_id, _host = await _dispatched_run(db_session, scenario)
    message_id = await _sent_while_working(conversation_id, run_id, "Use main.")
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)

    sent = await forward_queued_messages(uow_factory, agent_run_id=run_id)
    again = await forward_queued_messages(uow_factory, agent_run_id=run_id)

    assert (sent, again) == (1, 0), "a message already on its way was sent twice"
    (steer,) = await _commands(run_id, AgentHostCommandKind.STEER_RUN)
    assert steer.payload["message_id"] == str(message_id)
    assert STEER_DISPATCHED_AT in await _metadata(message_id)


async def test_what_the_host_says_about_a_steer_decides_who_answers_it(
    db_session, scenario
) -> None:
    """Delivered: this run answers it. Not delivered: it stays queued for the
    follow-up turn, and says why, so the person can take it back."""
    conversation_id, run_id, _host = await _dispatched_run(db_session, scenario)
    landed = await _sent_while_working(conversation_id, run_id, "Use main.")
    missed = await _sent_while_working(conversation_id, run_id, "And add tests.")
    uow_factory = SessionUnitOfWorkFactory(async_session_maker)
    assert await forward_queued_messages(uow_factory, agent_run_id=run_id) == 2

    await record_steer_result(
        uow_factory,
        agent_run_id=run_id,
        conversation_id=conversation_id,
        message_id=str(landed),
        payload={"delivered": True},
    )
    await record_steer_result(
        uow_factory,
        agent_run_id=run_id,
        conversation_id=conversation_id,
        message_id=str(missed),
        payload={"delivered": False, "detail": "the turn had ended"},
    )

    landed_metadata = await _metadata(landed)
    missed_metadata = await _metadata(missed)
    assert landed_metadata[STEERED_INTO_RUN] == str(run_id)
    assert STEER_UNDELIVERED not in landed_metadata
    assert STEERED_INTO_RUN not in missed_metadata
    assert missed_metadata[STEER_UNDELIVERED] == "the turn had ended"


async def test_a_run_the_host_has_not_accepted_keeps_its_messages_queued(
    db_session, scenario
) -> None:
    """A steer reaching a host before its START_RUN would be dropped there, so
    nothing is sent and nothing is marked -- the next check sends it."""
    conversation_id, run_id, _host = await _dispatched_run(
        db_session, scenario, state=AgentHostRunState.QUEUED_FOR_HOST
    )
    message_id = await _sent_while_working(conversation_id, run_id, "Use main.")

    sent = await forward_queued_messages(
        SessionUnitOfWorkFactory(async_session_maker), agent_run_id=run_id
    )

    assert sent == 0
    assert await _commands(run_id, AgentHostCommandKind.STEER_RUN) == []
    assert STEER_DISPATCHED_AT not in await _metadata(message_id)


# ------------------------------------------------------------------- sweeps


async def test_a_machine_is_told_to_stop_a_run_lemma_already_finished(
    db_session, scenario
) -> None:
    _, finished, host_id = await _dispatched_run(db_session, scenario)
    _, working, _ = await _dispatched_run(db_session, scenario)
    async with async_session_maker() as session:
        await session.execute(
            update(AgentRunModel)
            .where(AgentRunModel.id == finished)
            .values(status=AgentRunStatus.FAILED.value)
        )
        await session.commit()

    async with create_uow_from_session_maker(async_session_maker) as uow:
        told = await recovery.cancel_abandoned_host_runs(uow.session)
        await uow.commit()
    async with create_uow_from_session_maker(async_session_maker) as uow:
        told_again = await recovery.cancel_abandoned_host_runs(uow.session)
        await uow.commit()

    assert told == [host_id]
    assert told_again == [], "a second sweep piled on another cancel"
    assert len(await _commands(finished, AgentHostCommandKind.CANCEL_RUN)) == 1
    assert await _commands(working, AgentHostCommandKind.CANCEL_RUN) == []


async def test_a_lapsed_lease_goes_into_recovery_and_a_live_one_is_left(
    db_session, scenario
) -> None:
    """The first expiry only waits for the machine to come back; it neither
    fails the run nor cancels it, because the turn may still be going."""
    _, lapsed, _ = await _dispatched_run(db_session, scenario)
    _, live, _ = await _dispatched_run(db_session, scenario)
    async with async_session_maker() as session:
        await session.execute(
            update(AgentHostRunLeaseModel)
            .where(AgentHostRunLeaseModel.run_id == lapsed)
            .values(lease_expires_at=datetime.now(timezone.utc) - timedelta(minutes=1))
        )
        await session.commit()

    async with create_uow_from_session_maker(async_session_maker) as uow:
        moved = await recovery.reconcile_expired_leases(uow.session)
        await uow.commit()

    assert moved == 1
    recovering = await _lease(lapsed)
    untouched = await _lease(live)
    assert recovering is not None and untouched is not None
    assert recovering.state == AgentHostRunState.RECOVERING.value
    assert untouched.state == AgentHostRunState.RUNNING.value
    assert await _commands(lapsed, AgentHostCommandKind.CANCEL_RUN) == []


async def test_retention_collects_old_finished_dispatch_and_never_an_active_run(
    db_session, scenario
) -> None:
    _, old, _ = await _dispatched_run(db_session, scenario)
    _, recent, _ = await _dispatched_run(db_session, scenario)
    _, active, _ = await _dispatched_run(db_session, scenario)
    now = datetime.now(timezone.utc)
    async with async_session_maker() as session:
        for run_id, finished_at in ((old, now - timedelta(days=90)), (recent, now)):
            await session.execute(
                update(AgentHostRunLeaseModel)
                .where(AgentHostRunLeaseModel.run_id == run_id)
                .values(
                    state=AgentHostRunState.SUCCEEDED.value,
                    terminal_at=finished_at,
                )
            )
        await session.commit()

    async with create_uow_from_session_maker(async_session_maker) as uow:
        await recovery.cleanup_retained_state(uow.session)
        await uow.commit()

    assert await _lease(old) is None
    assert await _lease(recent) is not None
    assert await _lease(active) is not None
