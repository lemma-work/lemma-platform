from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4, uuid7

import pytest

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.infrastructure.models.conversation import ConversationModel
from app.modules.schedule.services.run_recovery_service import (
    ScheduleRunRecoveryService,
)
from app.modules.schedule.domain.schedule import ScheduleRunStatus
from app.modules.schedule.infrastructure.models.schedule import Schedule
from app.modules.schedule.infrastructure.models.run import ScheduleRun
from app.modules.schedule.repositories.schedule_run_repository import (
    ScheduleRunRepository,
)
from app.modules.schedule.services.run_outcome_service import (
    ScheduleRunOutcomeService,
)
from app.modules.schedule.tests.e2e.test_schedule_e2e import (
    _create_agent,
    _create_pod,
    _create_schedule,
    _create_workflow,
)

pytestmark = pytest.mark.e2e


async def test_run_ledger_concurrent_dedup(
    authenticated_client,
    fixed_test_org,
    db_manager,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 0 * * *"},
    )
    schedule_id = UUID(schedule["id"])
    user_id = UUID(schedule["user_id"])
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)

    async def claim_once():
        async with factory() as uow:
            return await ScheduleRunRepository(uow).claim(
                schedule_id=schedule_id,
                user_id=user_id,
                source_event_id="provider-event-42",
                target_kind="AGENT",
                payload={"ticket": 42},
                metadata={"provider": "test"},
                llm_output=None,
            )

    claims = await asyncio.gather(claim_once(), claim_once())
    winners = [claim for claim in claims if claim is not None]
    assert len(winners) == 1
    schedule_run = winners[0]

    async with factory() as uow:
        await ScheduleRunRepository(uow).mark_failed(
            schedule_run.id, RuntimeError("provider unavailable")
        )

    retried = await claim_once()
    assert retried is not None
    assert retried.id == schedule_run.id
    assert retried.target_run_id == schedule_run.target_run_id
    assert retried.attempts == 2
    async with factory() as uow:
        await ScheduleRunRepository(uow).mark_failed(
            retried.id, RuntimeError("provider still unavailable")
        )

    async with db_manager.session_factory() as session:
        persisted_schedule = await session.get(Schedule, schedule_id)
        assert persisted_schedule is not None
        assert persisted_schedule.consecutive_failures == 0

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule_id}/runs"
    )
    assert listed.status_code == 200, listed.text
    assert len(listed.json()["items"]) == 1
    assert listed.json()["items"][0]["status"] == "FAILED"


async def test_claim_adopts_owner_and_reserves_target_for_legacy_row(
    authenticated_client,
    fixed_test_org,
    db_manager,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 * * * *"},
    )
    schedule_id = UUID(schedule["id"])
    user_id = UUID(schedule["user_id"])
    source_event_id = "legacy-ownerless-ledger-row"
    stale = datetime.now(timezone.utc) - timedelta(minutes=5)

    async with db_manager.session_factory() as session, session.begin():
        session.add(
            ScheduleRun(
                schedule_id=schedule_id,
                user_id=None,
                source_event_id=source_event_id,
                status=ScheduleRunStatus.FAILED.value,
                attempts=1,
                target_kind="AGENT",
                target_run_id=None,
                payload={},
                fire_metadata={},
                llm_output={},
                completed_at=stale,
                created_at=stale,
                updated_at=stale,
            )
        )

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        claimed = await ScheduleRunRepository(uow).claim(
            schedule_id=schedule_id,
            user_id=user_id,
            source_event_id=source_event_id,
            target_kind="AGENT",
            payload={},
            metadata=None,
            llm_output=None,
        )

    assert claimed is not None
    assert claimed.user_id == user_id
    assert claimed.target_run_id is not None
    assert claimed.attempts == 2


async def test_legacy_terminal_status_is_not_reclaimed_and_is_normalized(
    authenticated_client,
    fixed_test_org,
    db_manager,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 * * * *"},
    )
    schedule_id = UUID(schedule["id"])
    user_id = UUID(schedule["user_id"])
    target_run_id = str(uuid4())
    legacy_id = uuid4()
    completed_at = datetime.now(timezone.utc)

    async with db_manager.session_factory() as session, session.begin():
        session.add(
            ScheduleRun(
                id=legacy_id,
                schedule_id=schedule_id,
                user_id=user_id,
                source_event_id="legacy-terminal-status",
                status=ScheduleRunStatus.TARGET_FAILED.value,
                attempts=1,
                target_kind="AGENT",
                target_run_id=target_run_id,
                payload={},
                fire_metadata={},
                llm_output={},
                completed_at=completed_at,
            )
        )

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        claimed = await ScheduleRunRepository(uow).claim(
            schedule_id=schedule_id,
            user_id=user_id,
            source_event_id="legacy-terminal-status",
            target_kind="AGENT",
            payload={},
            metadata=None,
            llm_output=None,
        )
        changed = await ScheduleRunOutcomeService(uow).record_target_outcome(
            target_kind="AGENT",
            target_run_id=target_run_id,
            status=ScheduleRunStatus.TARGET_FAILED,
            completed_at=completed_at,
            error_type="LegacyTargetFailure",
        )

    assert claimed is None
    assert changed is True
    async with db_manager.session_factory() as session:
        persisted = await session.get(ScheduleRun, legacy_id)
        persisted_schedule = await session.get(Schedule, schedule_id)
        assert persisted is not None
        assert persisted.status == ScheduleRunStatus.TARGET_FAILED.value
        assert persisted.target_outcome == ScheduleRunStatus.TARGET_FAILED.value
        assert persisted_schedule is not None
        assert persisted_schedule.consecutive_failures == 1


async def test_concurrent_duplicate_target_outcomes_count_once(
    authenticated_client,
    fixed_test_org,
    db_manager,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 0 * * *"},
    )
    schedule_id = UUID(schedule["id"])
    user_id = UUID(schedule["user_id"])
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)

    async with factory() as uow:
        schedule_run = await ScheduleRunRepository(uow).claim(
            schedule_id=schedule_id,
            user_id=user_id,
            source_event_id="concurrent-terminal-event",
            target_kind="AGENT",
            payload={},
            metadata=None,
            llm_output=None,
        )
        assert schedule_run is not None
        await ScheduleRunRepository(uow).mark_dispatched(schedule_run.id)

    async def fail_once() -> bool:
        async with factory() as uow:
            return await ScheduleRunOutcomeService(uow).record_target_outcome(
                target_kind="AGENT",
                target_run_id=schedule_run.target_run_id,
                status=ScheduleRunStatus.TARGET_FAILED,
                completed_at=None,
                error_type="AgentConversationFailed",
            )

    outcomes = await asyncio.gather(fail_once(), fail_once())
    assert sorted(outcomes) == [False, True]

    async with db_manager.session_factory() as session:
        persisted = await session.get(Schedule, schedule_id)
        assert persisted is not None
        assert persisted.consecutive_failures == 1


async def test_immediate_target_outcome_cannot_be_overwritten_by_dispatch(
    authenticated_client,
    fixed_test_org,
    db_manager,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 0 * * *"},
    )
    schedule_id = UUID(schedule["id"])
    user_id = UUID(schedule["user_id"])
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)

    async with factory() as uow:
        repository = ScheduleRunRepository(uow)
        schedule_run = await repository.claim(
            schedule_id=schedule_id,
            user_id=user_id,
            source_event_id="immediate-terminal-target",
            target_kind="WORKFLOW",
            payload={},
            metadata=None,
            llm_output=None,
        )
        assert schedule_run is not None
        changed = await ScheduleRunOutcomeService(uow).record_target_outcome(
            target_kind="WORKFLOW",
            target_run_id=schedule_run.target_run_id,
            status=ScheduleRunStatus.COMPLETED,
            completed_at=None,
        )
        # Dispatch lands after the target already finished. Its PROCESSING
        # predicate must make it a no-op rather than dragging the run back out
        # of a terminal state.
        await repository.mark_dispatched(schedule_run.id)

    assert changed is True

    listed = await authenticated_client.get(
        f"/pods/{pod_id}/schedules/{schedule_id}/runs"
    )
    assert listed.status_code == 200, listed.text
    persisted_run = listed.json()["items"][0]
    assert persisted_run["status"] == ScheduleRunStatus.COMPLETED.value
    assert persisted_run["completed_at"] is not None
    async with db_manager.session_factory() as session:
        stored = await session.get(ScheduleRun, schedule_run.id)
        assert stored is not None
        assert stored.status == ScheduleRunStatus.DISPATCHED.value
        assert stored.target_outcome == ScheduleRunStatus.COMPLETED.value


async def test_manual_retry_is_one_linked_child_and_is_idempotent(
    authenticated_client,
    fixed_test_org,
    db_manager,
    worker,
):
    _ = worker
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    workflow = await _create_workflow(
        authenticated_client,
        pod_id,
        start={"type": "SCHEDULED", "config": {"schedule_type": "CRON"}},
        name_prefix="manual-redrive-workflow",
    )
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        workflow_name=workflow["name"],
        config={"cron": "0 * * * *"},
    )
    source_id = uuid4()
    source_target_id = str(uuid4())
    async with db_manager.session_factory() as session, session.begin():
        session.add(
            ScheduleRun(
                id=source_id,
                schedule_id=UUID(schedule["id"]),
                user_id=UUID(schedule["user_id"]),
                source_event_id="manual-redrive-source",
                status=ScheduleRunStatus.DEAD_LETTERED.value,
                attempts=10,
                target_kind="WORKFLOW",
                target_run_id=source_target_id,
                payload={"source": "manual-redrive"},
                fire_metadata={},
                llm_output={},
                completed_at=datetime.now(timezone.utc),
            )
        )

    path = f"/pods/{pod_id}/schedules/{schedule['id']}/runs/{source_id}/retry"
    first = await authenticated_client.post(path)
    second = await authenticated_client.post(path)

    assert first.status_code == 202, first.text
    assert second.status_code == 202, second.text
    assert first.json()["id"] == second.json()["id"]
    assert first.json()["id"] != str(source_id)
    assert first.json()["target_run_id"] != source_target_id
    assert first.json()["redrive_of_run_id"] == str(source_id)

    async with db_manager.session_factory() as session:
        original = await session.get(ScheduleRun, source_id)
        assert original is not None
        assert original.status == ScheduleRunStatus.DEAD_LETTERED.value
        assert original.attempts == 10


async def test_breaker_recompute_is_independent_of_event_delivery_order(
    authenticated_client,
    fixed_test_org,
    db_manager,
):
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 * * * *"},
    )
    schedule_id = UUID(schedule["id"])
    user_id = UUID(schedule["user_id"])
    base = datetime(2026, 1, 1, tzinfo=timezone.utc)
    factory = SessionUnitOfWorkFactory(db_manager.session_factory)

    async with factory() as uow:
        for index, (outcome, completed_at) in enumerate(
            [
                (ScheduleRunStatus.TARGET_FAILED, base + timedelta(minutes=20)),
                (ScheduleRunStatus.COMPLETED, base + timedelta(minutes=10)),
                (ScheduleRunStatus.TARGET_FAILED, base + timedelta(minutes=30)),
                (ScheduleRunStatus.TARGET_FAILED, base + timedelta(minutes=5)),
            ]
        ):
            uow.session.add(
                ScheduleRun(
                    schedule_id=schedule_id,
                    user_id=user_id,
                    source_event_id=f"ordered-outcome-{index}",
                    status=ScheduleRunStatus.DISPATCHED.value,
                    attempts=1,
                    target_kind="AGENT",
                    target_run_id=str(uuid4()),
                    target_outcome=outcome.value,
                    payload={},
                    fire_metadata={},
                    llm_output={},
                    completed_at=completed_at,
                )
            )
        await uow.session.flush()
        await ScheduleRunOutcomeService(uow).recompute_breaker(schedule_id)

    async with db_manager.session_factory() as session:
        persisted = await session.get(Schedule, schedule_id)
        assert persisted is not None
        assert persisted.consecutive_failures == 2

    async with factory() as uow:
        uow.session.add(
            ScheduleRun(
                schedule_id=schedule_id,
                user_id=user_id,
                source_event_id="newer-cancellation",
                status=ScheduleRunStatus.DISPATCHED.value,
                attempts=1,
                target_kind="AGENT",
                target_run_id=str(uuid4()),
                target_outcome=ScheduleRunStatus.CANCELLED.value,
                payload={},
                fire_metadata={},
                llm_output={},
                completed_at=base + timedelta(minutes=40),
            )
        )
        await uow.session.flush()
        await ScheduleRunOutcomeService(uow).recompute_breaker(schedule_id)

    async with db_manager.session_factory() as session:
        persisted = await session.get(Schedule, schedule_id)
        assert persisted is not None
        assert persisted.consecutive_failures == 0


async def _agent_schedule(
    authenticated_client, fixed_test_org
) -> tuple[str, UUID, UUID]:
    pod_id = await _create_pod(authenticated_client, fixed_test_org["id"])
    agent = await _create_agent(authenticated_client, pod_id)
    schedule = await _create_schedule(
        authenticated_client,
        pod_id,
        schedule_type="TIME",
        agent_name=agent["name"],
        config={"cron": "0 * * * *"},
    )
    return pod_id, UUID(schedule["id"]), UUID(schedule["user_id"])


def _ledger_row(
    schedule_id: UUID,
    user_id: UUID,
    *,
    tag: str,
    stale: datetime,
    status: ScheduleRunStatus = ScheduleRunStatus.DISPATCHED,
    target_kind: str = "AGENT",
    target_run_id: UUID | None = None,
    row_id: UUID | None = None,
) -> ScheduleRun:
    return ScheduleRun(
        id=row_id or uuid7(),
        schedule_id=schedule_id,
        user_id=user_id,
        source_event_id=tag,
        status=status.value,
        attempts=1,
        target_kind=target_kind,
        target_run_id=str(target_run_id or uuid4()),
        payload={},
        fire_metadata={},
        llm_output={},
        started_at=stale,
        created_at=stale,
        updated_at=stale,
    )


async def test_recovery_settles_only_dispatched_agent_runs_whose_outcome_was_lost(
    authenticated_client,
    fixed_test_org,
    db_manager,
):
    """The sweep is now only the net under the agent outcome stream.

    A lost terminal outcome is reconciled and a conversation that no longer
    exists is dead-lettered. Rows other owners now settle -- a workflow target
    (its end is a fact in the run's own transaction), and PROCESSING or FAILED
    reservations (their ``schedule.start_target`` item retries them) -- are not
    touched at all.
    """
    pod_id, schedule_id, user_id = await _agent_schedule(
        authenticated_client, fixed_test_org
    )
    stale = datetime.now(timezone.utc) - timedelta(minutes=10)
    finished_conversation = uuid4()
    lost_id, gone_id = uuid7(), uuid7()
    workflow_id, processing_id, failed_id = uuid7(), uuid7(), uuid7()

    async with db_manager.session_factory() as session, session.begin():
        session.add(
            ConversationModel(
                id=finished_conversation,
                user_id=user_id,
                pod_id=UUID(pod_id),
                status="COMPLETED",
            )
        )
        await session.flush()
        session.add_all(
            [
                _ledger_row(
                    schedule_id,
                    user_id,
                    tag="lost-terminal-outcome",
                    stale=stale,
                    target_run_id=finished_conversation,
                    row_id=lost_id,
                ),
                _ledger_row(
                    schedule_id, user_id, tag="gone", stale=stale, row_id=gone_id
                ),
                _ledger_row(
                    schedule_id,
                    user_id,
                    tag="workflow-target",
                    stale=stale,
                    target_kind="WORKFLOW",
                    row_id=workflow_id,
                ),
                _ledger_row(
                    schedule_id,
                    user_id,
                    tag="processing",
                    stale=stale,
                    status=ScheduleRunStatus.PROCESSING,
                    row_id=processing_id,
                ),
                _ledger_row(
                    schedule_id,
                    user_id,
                    tag="failed",
                    stale=stale,
                    status=ScheduleRunStatus.FAILED,
                    row_id=failed_id,
                ),
            ]
        )

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        result = await ScheduleRunRecoveryService(uow).recover()

    assert (result.reconciled, result.dead_lettered, result.still_running) == (1, 1, 0)
    async with db_manager.session_factory() as session:
        lost = await session.get(ScheduleRun, lost_id)
        gone = await session.get(ScheduleRun, gone_id)
        assert lost is not None and lost.status == "DISPATCHED"
        assert lost.target_outcome == "COMPLETED"
        assert gone is not None and gone.status == "DEAD_LETTERED"
        assert gone.error_type == "ScheduleTargetMissing"
        for untouched_id, status in (
            (workflow_id, "DISPATCHED"),
            (processing_id, "PROCESSING"),
            (failed_id, "FAILED"),
        ):
            row = await session.get(ScheduleRun, untouched_id)
            assert row is not None
            assert row.status == status
            assert row.target_outcome is None
            assert row.last_inspected_at is None, "the sweep must not even look"


async def test_recovery_advances_past_runs_whose_targets_are_still_alive(
    authenticated_client,
    fixed_test_org,
    db_manager,
):
    """The sweep must not spend every tick on the same in-flight runs.

    A run whose agent is alive but unfinished needs no change, so without the
    inspection stamp no UPDATE fires and the next tick selects the same rows:
    a standing set of long-running agents would hold the batch while lost
    outcomes waited behind it forever.
    """
    pod_id, schedule_id, user_id = await _agent_schedule(
        authenticated_client, fixed_test_org
    )
    stale = datetime.now(timezone.utc) - timedelta(minutes=30)
    running_conversation, finished_conversation = uuid4(), uuid4()
    # uuid7: both rows tie on `last_inspected_at` (NULL), so the id tie-break
    # decides which one a `limit=1` sweep reaches first.
    parked_id = uuid7()
    behind_it_id = uuid7()

    async with db_manager.session_factory() as session, session.begin():
        session.add_all(
            [
                ConversationModel(
                    id=running_conversation,
                    user_id=user_id,
                    pod_id=UUID(pod_id),
                    status="RUNNING",
                ),
                ConversationModel(
                    id=finished_conversation,
                    user_id=user_id,
                    pod_id=UUID(pod_id),
                    status="COMPLETED",
                ),
            ]
        )
        await session.flush()
        session.add_all(
            [
                _ledger_row(
                    schedule_id,
                    user_id,
                    tag="parked-on-a-running-agent",
                    stale=stale,
                    target_run_id=running_conversation,
                    row_id=parked_id,
                ),
                _ledger_row(
                    schedule_id,
                    user_id,
                    tag="lost-outcome-behind-the-wall",
                    stale=stale,
                    target_run_id=finished_conversation,
                    row_id=behind_it_id,
                ),
            ]
        )

    factory = SessionUnitOfWorkFactory(db_manager.session_factory)
    async with factory() as uow:
        first = await ScheduleRunRecoveryService(uow).recover(limit=1)
    assert first.still_running == 1, "the parked run is in flight, not reconciled"
    assert first.reconciled == 0, "nothing was reconciled, so nothing may claim it was"

    async with factory() as uow:
        second = await ScheduleRunRecoveryService(uow).recover(limit=1)
    assert second.reconciled == 1, "the second tick must get past the parked run"

    async with db_manager.session_factory() as session:
        parked = await session.get(ScheduleRun, parked_id)
        behind = await session.get(ScheduleRun, behind_it_id)
        assert parked is not None
        assert parked.last_inspected_at is not None, "an inspection must be recorded"
        assert parked.target_outcome is None, "a live target has no outcome yet"
        assert behind is not None
        assert behind.target_outcome == "COMPLETED", "the lost outcome was recovered"
