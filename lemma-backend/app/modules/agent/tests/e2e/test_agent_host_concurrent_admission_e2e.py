"""Many runs starting on one Agent Host at once, with the host's own traffic in flight.

A host takes up to twenty runs at a time. Started together against one host,
half of them failed within seconds on a Postgres deadlock. Three kinds of
transaction lock the same ``agent_hosts`` row:

- a run start, which reads the host to admit the run and inserts its
  ``START_RUN`` command;
- a credential refresh, which locks a running run's lease and inserts a
  ``REFRESH_CREDENTIAL`` command -- and every command insert takes a key-share
  lock on the host row through its foreign key;
- the host's ``control`` frame, which rewrites the heartbeat row and then locks
  the leases of the runs it reports on.

The frame and the refresh take the host row and a lease in opposite orders.
That was harmless only while neither lock conflicted with the other, and both
the heartbeat and admission used ``FOR UPDATE``, the one row lock a foreign
key's ``FOR KEY SHARE`` waits for. These tests pin the locks down: the
choreographed one fails on a deadlock with the old locks, the crowd shows the
fix under realistic load, and the last two show that weakening admission's lock
did not open a gap for a revoke to slip through.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.agent_host.domain.host import (
    TERMINAL_AGENT_HOST_RUN_STATES,
    AgentHostCommandKind,
    AgentHostCommandState,
    AgentHostRunCheckpoint,
    AgentHostRunSpec,
    AgentHostRunState,
    HostHello,
)
from app.modules.agent.agent_host.control.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.agent_host.control.repository import (
    AgentHostRepository,
)
from app.modules.agent.agent_host.control.repository_common import (
    AgentHostRunConflict,
)
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostRunLeaseModel,
    AgentRuntimeProfileModel,
)
from app.modules.agent.tests.e2e.agent_host_helpers import (
    conversation_with_a_leased_run,
    paired_machine,
)
from app.modules.test_support.e2e.builders import E2EScenario
from app.modules.test_support.e2e.waiters import eventually

pytestmark = [pytest.mark.e2e]

_MAX_RUNS = 20


class _Host:
    """One paired host with one ready harness, and a profile that targets it."""

    def __init__(self, machine: dict[str, object], profile_id: UUID) -> None:
        self.host_id = UUID(str(machine["host_id"]))
        self.harness_id = UUID(str(machine["harness_id"]))
        self.hello = HostHello.model_validate(machine["hello"])
        self.profile_id = profile_id


async def _host(db_session, scenario: E2EScenario, *, name: str) -> _Host:
    await scenario.create_org_with_pod(name_prefix=name)
    machine = await paired_machine(scenario, display_name=f"{name} MacBook")
    profile = AgentRuntimeProfileModel(
        organization_id=UUID(scenario.org_id),
        runtime_type="HARNESS",
        harness_id=machine["harness_id"],
        user_id=UUID(scenario.owner_user["id"]),
        scope="PERSONAL",
        kind="HARNESS",
        protocol="AGENT_HOST",
        name=f"{name} profile {uuid4().hex[:8]}",
        default_model_name="gpt-5-codex",
        model_catalog=[],
        config={"harness_id": str(machine["harness_id"]), "config_options": []},
    )
    db_session.add(profile)
    await db_session.commit()
    return _Host(machine, profile.id)


async def _unstarted_run(db_session, scenario: E2EScenario) -> tuple[UUID, UUID]:
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations", json={"title": "crowd"}
    )
    assert created.status_code in {200, 201}, created.text
    conversation_id = UUID(created.json()["id"])
    run = AgentRunModel(
        conversation_id=conversation_id,
        status="RUNNING",
        started_at=datetime.now(timezone.utc),
    )
    db_session.add(run)
    await db_session.commit()
    return run.id, conversation_id


async def _running_run(db_session, scenario: E2EScenario, host: _Host) -> UUID:
    _, run_id = await conversation_with_a_leased_run(
        db_session,
        scenario,
        host_id=host.host_id,
        harness_id=host.harness_id,
        state=AgentHostRunState.ACCEPTED,
    )
    await db_session.commit()
    return run_id


def _spec(run_id: UUID, conversation_id: UUID, host: _Host) -> AgentHostRunSpec:
    return AgentHostRunSpec(
        agent_run_id=run_id,
        conversation_id=conversation_id,
        harness_id=host.harness_id,
        profile_revision="rev-1",
        model_name="gpt-5-codex",
        config_selections={},
        system_prompt="Concurrent admission test.",
        prompt=[{"type": "text", "text": "Hello."}],
        run_deadline=datetime.now(timezone.utc) + timedelta(minutes=30),
    )


async def _admit(uow: SqlAlchemyUnitOfWork, host: _Host, spec: AgentHostRunSpec):
    return await AgentHostDispatchRepository(uow).enqueue_run(
        host_id=host.host_id,
        harness_id=host.harness_id,
        runtime_profile_id=host.profile_id,
        run_spec=spec,
        encrypted_mcp_payload={"encrypted": True},
    )


async def _refresh(uow: SqlAlchemyUnitOfWork, run_id: UUID):
    return await AgentHostDispatchRepository(uow).enqueue_credential_refresh(
        run_id=run_id, encrypted_mcp_payload={"encrypted": True}
    )


async def _control_frame(
    uow: SqlAlchemyUnitOfWork, host: _Host, *, active: int, reporting: list[UUID]
) -> None:
    """What ``AgentHostLinkStore.apply_control`` runs for one frame, minus its retry.

    The heartbeat goes first and is a real write -- the capacity differs from
    the last one -- and the checkpoints for the runs it reports on lock their
    leases after it.
    """
    await AgentHostRepository(uow).mark_seen(
        host_id=host.host_id,
        hello=host.hello,
        capacity={
            "max_runs": _MAX_RUNS,
            "active_runs": active,
            "available_runs": _MAX_RUNS - active,
        },
    )
    await AgentHostDispatchRepository(uow).poll_commands(
        host_id=host.host_id,
        limit=50,
        acknowledged_command_ids=[],
        checkpoints=[
            AgentHostRunCheckpoint(
                run_id=run_id, lease_epoch=1, state=AgentHostRunState.RUNNING
            )
            for run_id in reporting
        ],
        rejections=[],
        available_run_slots=_MAX_RUNS - active,
    )


async def _in_own_transaction(
    work: Callable[[SqlAlchemyUnitOfWork], Awaitable[object]],
) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await work(uow)
        await uow.commit()


async def _waiting_on_a_lock(table: str) -> int:
    """Backends blocked on a row lock while running a statement against ``table``."""
    async with async_session_maker() as session:
        return (
            await session.execute(
                text(
                    "SELECT count(*) FROM pg_stat_activity "
                    "WHERE datname = current_database() "
                    "AND wait_event_type = 'Lock' "
                    "AND query ILIKE :table"
                ),
                {"table": f"%{table}%"},
            )
        ).scalar_one()


async def _commands(run_ids: list[UUID], kind: AgentHostCommandKind):
    async with async_session_maker() as session:
        return list(
            (
                await session.execute(
                    select(AgentHostCommandModel).where(
                        AgentHostCommandModel.run_id.in_(run_ids),
                        AgentHostCommandModel.kind == kind.value,
                    )
                )
            ).scalars()
        )


async def test_a_heartbeat_and_a_credential_refresh_on_one_run_do_not_deadlock(
    db_session, scenario
) -> None:
    """The cycle, made certain rather than likely.

    The refresh holds the run's lease. The host's frame rewrites its heartbeat
    row and then waits for that lease. The refresh's command insert then needs
    a key-share lock on the host row the heartbeat is holding. With
    ``FOR UPDATE`` on the heartbeat that is a cycle, and Postgres aborts one
    side; with ``FOR NO KEY UPDATE`` the insert does not wait at all.
    """
    host = await _host(db_session, scenario, name="Refresh")
    running = await _running_run(db_session, scenario, host)

    async with create_uow_from_session_maker(async_session_maker) as refresh:
        await refresh.session.get(AgentHostRunLeaseModel, running, with_for_update=True)
        frame = asyncio.create_task(
            _in_own_transaction(
                lambda uow: _control_frame(uow, host, active=1, reporting=[running])
            )
        )
        await eventually(
            label="the control frame waiting on the refresh's lease",
            probe=lambda: _waiting_on_a_lock("agent_host_run_leases"),
            done=lambda waiting: waiting >= 1,
            timeout_seconds=15,
        )
        assert not frame.done(), "the frame never took the heartbeat row"
        command = await asyncio.wait_for(_refresh(refresh, running), timeout=30)
        assert command is not None
        await refresh.commit()

    await asyncio.wait_for(frame, timeout=30)
    assert len(await _commands([running], AgentHostCommandKind.REFRESH_CREDENTIAL)) == 1


async def test_twenty_runs_start_on_one_host_alongside_its_traffic(
    db_session, scenario
) -> None:
    """Twenty starts, refreshes for the runs already going, and the host's frames.

    Every transaction runs raw -- none of the retries production wraps them in
    -- so a deadlock anywhere fails the test rather than being absorbed.
    """
    host = await _host(db_session, scenario, name="Crowd")
    running = [await _running_run(db_session, scenario, host) for _ in range(4)]
    starting = [await _unstarted_run(db_session, scenario) for _ in range(_MAX_RUNS)]

    work: list[Callable[[SqlAlchemyUnitOfWork], Awaitable[object]]] = []
    for index, (run_id, conversation_id) in enumerate(starting):
        spec = _spec(run_id, conversation_id, host)
        work.append(lambda uow, spec=spec: _admit(uow, host, spec))
        run = running[index % len(running)]
        work.append(lambda uow, run=run: _refresh(uow, run))
        if index % 2 == 0:
            work.append(
                lambda uow, active=index: _control_frame(
                    uow, host, active=active % _MAX_RUNS, reporting=running
                )
            )

    outcomes = await asyncio.gather(
        *(_in_own_transaction(each) for each in work), return_exceptions=True
    )

    failures = [outcome for outcome in outcomes if isinstance(outcome, BaseException)]
    assert not failures, f"{len(failures)} of {len(work)} failed: {failures[:3]!r}"
    started = await _commands(
        [run_id for run_id, _ in starting], AgentHostCommandKind.START_RUN
    )
    assert len(started) == _MAX_RUNS
    refreshed = await _commands(running, AgentHostCommandKind.REFRESH_CREDENTIAL)
    assert len(refreshed) == _MAX_RUNS


async def test_a_revoke_waits_for_an_admission_in_flight_and_then_ends_its_run(
    db_session, scenario
) -> None:
    """Admission's shared lock still holds a revoke off until it commits.

    Then the revoke sees the run admission just queued and ends it, so a run
    can never be left waiting on a computer that was removed.
    """
    host = await _host(db_session, scenario, name="Revoke after")
    run_id, conversation_id = await _unstarted_run(db_session, scenario)

    async with create_uow_from_session_maker(async_session_maker) as admission:
        await _admit(admission, host, _spec(run_id, conversation_id, host))
        revoke = asyncio.create_task(
            _in_own_transaction(
                lambda uow: AgentHostRepository(uow).revoke(
                    host_id=host.host_id, user_id=UUID(scenario.owner_user["id"])
                )
            )
        )
        await eventually(
            label="the revoke waiting on the admission's host lock",
            probe=lambda: _waiting_on_a_lock("agent_hosts"),
            done=lambda waiting: waiting >= 1,
            timeout_seconds=15,
        )
        assert not revoke.done(), "a revoke went through an admission in flight"
        await admission.commit()

    await asyncio.wait_for(revoke, timeout=30)
    async with async_session_maker() as session:
        lease = await session.get(AgentHostRunLeaseModel, run_id)
    assert lease is not None
    assert AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES
    (command,) = await _commands([run_id], AgentHostCommandKind.START_RUN)
    assert command.state == AgentHostCommandState.CANCELLED.value


async def test_an_admission_waits_for_a_revoke_in_flight_and_then_refuses(
    db_session, scenario
) -> None:
    host = await _host(db_session, scenario, name="Revoke before")
    run_id, conversation_id = await _unstarted_run(db_session, scenario)

    async with create_uow_from_session_maker(async_session_maker) as revoking:
        await AgentHostRepository(revoking).revoke(
            host_id=host.host_id, user_id=UUID(scenario.owner_user["id"])
        )
        admission = asyncio.create_task(
            _in_own_transaction(
                lambda uow: _admit(uow, host, _spec(run_id, conversation_id, host))
            )
        )
        await eventually(
            label="the admission waiting on the revoke's host lock",
            probe=lambda: _waiting_on_a_lock("agent_hosts"),
            done=lambda waiting: waiting >= 1,
            timeout_seconds=15,
        )
        assert not admission.done(), "an admission read the host mid-revoke"
        await revoking.commit()

    with pytest.raises(AgentHostRunConflict, match="was removed from Lemma"):
        await asyncio.wait_for(admission, timeout=30)
    async with async_session_maker() as session:
        assert await session.get(AgentHostRunLeaseModel, run_id) is None
