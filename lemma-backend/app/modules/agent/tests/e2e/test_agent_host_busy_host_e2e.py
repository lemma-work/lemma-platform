"""A run queued behind a full computer waits for a slot, and starts when one frees.

A host refuses a START_RUN while every one of its run slots is taken, and Lemma
does not hand it one while the host reports none free, so the run's lease sits
QUEUED_FOR_HOST. It used to fail at the host-wait timeout -- "No Agent Host
received the run before its wait deadline" -- with the computer right there and
busy. These pin the hold against real Postgres: past that timeout the run keeps
its place and says what it waits on, the sweep leaves it alone, and the first
free slot starts it. The last one pins the lock order: the hold never waits for
a START_RUN that a host's control frame holds while waiting for the lease.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, update

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.agent_host.control import admission
from app.modules.agent.agent_host.domain.host import (
    AgentHostCommandKind,
    AgentHostCommandState,
    AgentHostRunSpec,
    AgentHostRunState,
    HostHello,
)
from app.modules.agent.domain.value_objects import AgentEventType
from app.modules.agent.agent_host.control import recovery
from app.modules.agent.agent_host.control.dispatch_repository import (
    AgentHostDispatchRepository,
)
from app.modules.agent.agent_host.control.repository import (
    AgentHostRepository,
)
from app.modules.agent.agent_host.harness.slot_wait import SlotWait
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostRunLeaseModel,
    AgentRuntimeProfileModel,
)
from app.modules.agent.tests.e2e.agent_host_helpers import paired_machine

pytestmark = [pytest.mark.e2e]

_MAX_RUNS = 20
#: The profile's host-wait timeout, short so "past it" is a few seconds away.
_WAIT_SECONDS = 2


async def _queued_run(db_session, scenario) -> tuple[dict, UUID]:
    """A host reporting every slot taken, and one more run admitted to it."""
    await scenario.create_org_with_pod(name_prefix="BusyHost")
    machine = await paired_machine(scenario, display_name="Ada's MacBook")
    profile = AgentRuntimeProfileModel(
        organization_id=UUID(scenario.org_id),
        runtime_type="HARNESS",
        harness_id=machine["harness_id"],
        user_id=UUID(scenario.owner_user["id"]),
        scope="PERSONAL",
        kind="HARNESS",
        protocol="AGENT_HOST",
        name=f"Busy profile {uuid4().hex[:8]}",
        default_model_name="gpt-5-codex",
        model_catalog=[],
        config={"harness_id": str(machine["harness_id"]), "config_options": []},
    )
    db_session.add(profile)
    created = await scenario.owner_client.post(
        f"/pods/{scenario.pod_id}/conversations", json={"title": "busy"}
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

    await _heartbeat(machine, active=_MAX_RUNS)
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await admission.enqueue_run(
            uow,
            host_id=machine["host_id"],
            harness_id=machine["harness_id"],
            runtime_profile_id=profile.id,
            run_spec=AgentHostRunSpec(
                agent_run_id=run.id,
                conversation_id=conversation_id,
                harness_id=machine["harness_id"],
                profile_revision="rev-1",
                model_name="gpt-5-codex",
                config_selections={},
                system_prompt="Busy host test.",
                prompt=[{"type": "text", "text": "Hello."}],
                run_deadline=datetime.now(timezone.utc) + timedelta(minutes=30),
            ),
            encrypted_mcp_payload={"encrypted": True},
            command_ttl_seconds=_WAIT_SECONDS,
        )
        await uow.commit()
    return machine, run.id


async def _heartbeat(machine: dict, *, active: int) -> None:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await AgentHostRepository(uow).mark_seen(
            host_id=machine["host_id"],
            hello=HostHello.model_validate(machine["hello"]),
            capacity={
                "max_runs": _MAX_RUNS,
                "active_runs": active,
                "available_runs": _MAX_RUNS - active,
            },
        )
        await uow.commit()


async def _poll(machine: dict, *, free: int) -> list:
    async with create_uow_from_session_maker(async_session_maker) as uow:
        commands = await AgentHostDispatchRepository(uow).poll_commands(
            host_id=machine["host_id"],
            limit=10,
            acknowledged_command_ids=[],
            checkpoints=[],
            rejections=[],
            available_run_slots=free,
        )
        await uow.commit()
    return [c for c in commands if c.kind == AgentHostCommandKind.START_RUN]


async def _start_runs(run_id: UUID) -> list[AgentHostCommandModel]:
    async with async_session_maker() as session:
        return list(
            (
                await session.execute(
                    select(AgentHostCommandModel).where(
                        AgentHostCommandModel.run_id == run_id,
                        AgentHostCommandModel.kind
                        == AgentHostCommandKind.START_RUN.value,
                    )
                )
            ).scalars()
        )


async def _lease(run_id: UUID) -> AgentHostRunLeaseModel:
    async with async_session_maker() as session:
        lease = await session.get(AgentHostRunLeaseModel, run_id)
        assert lease is not None
        return lease


async def test_a_run_behind_a_full_host_waits_past_the_timeout_then_starts(
    db_session, scenario
) -> None:
    machine, run_id = await _queued_run(db_session, scenario)
    uow_factory = lambda: create_uow_from_session_maker(async_session_maker)  # noqa: E731
    slot_wait = SlotWait(wait_seconds=_WAIT_SECONDS)

    # A full host is not handed the run.
    assert await _poll(machine, free=0) == []

    # The harness's first check finds the host full, and says so.
    held, notice = await slot_wait.check(
        uow_factory, AgentHostDispatchRepository, agent_run_id=run_id
    )
    assert held
    assert notice is not None
    assert (notice.type, notice.data["detail"]) == (
        AgentEventType.STATUS,
        "Waiting for a free slot on Ada's MacBook: 20 runs already going",
    )

    # Its later checks, every second on a clock run forward to well past the
    # host-wait timeout, keep renewing the run's place.
    start = datetime.now(timezone.utc)
    later = [
        start + timedelta(seconds=second) for second in range(1, 3 * _WAIT_SECONDS + 1)
    ]
    for now in later:
        async with create_uow_from_session_maker(async_session_maker) as uow:
            assert await recovery.hold_for_busy_host(
                uow.session, run_id=run_id, wait_seconds=_WAIT_SECONDS, now=now
            )
            await uow.commit()
    # Nothing new to say while nothing changed.
    held, notice = await slot_wait.check(
        uow_factory, AgentHostDispatchRepository, agent_run_id=run_id
    )
    assert held
    assert notice is None

    # Past the timeout, the sweep that fails lapsed leases leaves it be, and
    # its START_RUN is still there to be handed out.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        await recovery.reconcile_expired_leases(uow.session, now=later[-1])
        await uow.commit()
    lease = await _lease(run_id)
    assert lease.state == AgentHostRunState.QUEUED_FOR_HOST.value
    assert lease.lease_expires_at > later[-1]
    (start_run,) = await _start_runs(run_id)
    assert start_run.state == AgentHostCommandState.QUEUED.value
    assert start_run.expires_at > later[-1]

    # A slot frees: the next poll hands the run out.
    await _heartbeat(machine, active=_MAX_RUNS - 1)
    held, _ = await slot_wait.check(
        uow_factory, AgentHostDispatchRepository, agent_run_id=run_id
    )
    assert not held
    started = await _poll(machine, free=1)
    assert [command.run_id for command in started] == [run_id]
    assert (await _lease(run_id)).state == AgentHostRunState.LEASED.value


async def test_the_hold_does_not_wait_for_a_start_run_the_control_frame_holds(
    db_session, scenario
) -> None:
    """The frame locks the START_RUN, then waits for the lease.

    The hold has the lease by then. Waiting for the command too would be a
    cycle Postgres breaks by aborting one side; it skips the command instead,
    and the next check renews it.
    """
    machine, run_id = await _queued_run(db_session, scenario)
    async with create_uow_from_session_maker(async_session_maker) as hold_uow:
        await hold_uow.session.execute(
            update(AgentHostRunLeaseModel)
            .where(AgentHostRunLeaseModel.run_id == run_id)
            .values(lease_expires_at=datetime.now(timezone.utc))
        )
        # Both due, so the hold wants to renew the command as well.
        await hold_uow.session.execute(
            update(AgentHostCommandModel)
            .where(AgentHostCommandModel.run_id == run_id)
            .values(expires_at=datetime.now(timezone.utc))
        )
        await hold_uow.commit()

    async with create_uow_from_session_maker(async_session_maker) as frame:
        command = (
            await frame.session.execute(
                AgentHostCommandModel.__table__.select()
                .where(
                    AgentHostCommandModel.run_id == run_id,
                    AgentHostCommandModel.kind == AgentHostCommandKind.START_RUN.value,
                )
                .with_for_update()
            )
        ).one()
        assert command.state == AgentHostCommandState.QUEUED.value

        async with create_uow_from_session_maker(async_session_maker) as hold_uow:
            busy = await asyncio.wait_for(
                recovery.hold_for_busy_host(
                    hold_uow.session, run_id=run_id, wait_seconds=_WAIT_SECONDS
                ),
                timeout=10,
            )
            assert busy is not None
            # Now the frame's lease lock would wait on the hold; it is the
            # hold's commit that lets it through, not an abort.
            frame_lease = asyncio.create_task(
                frame.session.get(AgentHostRunLeaseModel, run_id, with_for_update=True)
            )
            await asyncio.sleep(0.5)
            await hold_uow.commit()
        assert (await asyncio.wait_for(frame_lease, timeout=10)) is not None
        await frame.commit()


async def test_a_start_run_held_back_by_a_full_host_lives_as_long_as_its_lease(
    db_session, scenario
) -> None:
    """The poll that holds a START_RUN back keeps it alive with its lease.

    The hold skips a command a poll has locked, so without this the command
    could expire between checks and the run be stranded once a slot frees.
    """
    machine, run_id = await _queued_run(db_session, scenario)
    soon = datetime.now(timezone.utc) + timedelta(seconds=5)
    later = datetime.now(timezone.utc) + timedelta(minutes=20)
    async with async_session_maker() as session:
        await session.execute(
            update(AgentHostRunLeaseModel)
            .where(AgentHostRunLeaseModel.run_id == run_id)
            .values(lease_expires_at=later)
        )
        await session.execute(
            update(AgentHostCommandModel)
            .where(
                AgentHostCommandModel.run_id == run_id,
                AgentHostCommandModel.kind == AgentHostCommandKind.START_RUN.value,
            )
            .values(expires_at=soon)
        )
        await session.commit()

    assert await _poll(machine, free=0) == []

    (start,) = await _start_runs(run_id)
    assert start.state == AgentHostCommandState.QUEUED.value
    assert start.expires_at == later


async def test_a_start_run_whose_run_stopped_waiting_is_not_kept_alive(
    db_session, scenario
) -> None:
    """Only a lease still queued for the host carries its command along."""
    machine, run_id = await _queued_run(db_session, scenario)
    soon = datetime.now(timezone.utc) + timedelta(seconds=5)
    async with async_session_maker() as session:
        await session.execute(
            update(AgentHostRunLeaseModel)
            .where(AgentHostRunLeaseModel.run_id == run_id)
            .values(
                state=AgentHostRunState.CANCELLED.value,
                lease_expires_at=datetime.now(timezone.utc) + timedelta(minutes=20),
            )
        )
        await session.execute(
            update(AgentHostCommandModel)
            .where(
                AgentHostCommandModel.run_id == run_id,
                AgentHostCommandModel.kind == AgentHostCommandKind.START_RUN.value,
            )
            .values(expires_at=soon)
        )
        await session.commit()

    await _poll(machine, free=0)

    (start,) = await _start_runs(run_id)
    assert start.expires_at == soon
