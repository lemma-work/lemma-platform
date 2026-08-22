"""Lease recovery and retention for Agent Host dispatch.

These are plain functions over a session rather than a mixin. The previous
shape was a mixin with exactly one consumer, which is inheritance used to split
a file: it adds indirection without adding polymorphism.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.modules.agent.domain.agent_host import (
    TERMINAL_AGENT_HOST_RUN_STATES,
    AgentHostCommandKind,
    AgentHostCommandState,
    AgentHostRunState,
)
from app.modules.agent.domain.value_objects import TERMINAL_AGENT_RUN_STATUSES
from app.modules.agent.infrastructure.agent_host.repository_common import (
    DEFAULT_COMMAND_TTL_SECONDS,
    utcnow,
)
from app.modules.agent.infrastructure.models import AgentRunModel
from app.modules.agent.infrastructure.repository_status import (
    run_status_values_for_db,
)
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostPairingModel,
    AgentHostRunLeaseModel,
)


# Pairing artifacts are single-use and short-lived. Commands and leases
# document dispatch history and are kept longer.
_TRANSIENT_RETENTION = timedelta(hours=24)
_DISPATCH_RETENTION = timedelta(days=30)

# How long an accepted-but-silent host has to reconnect before its run is
# declared unknown rather than retried.
DEFAULT_RECOVERY_GRACE_SECONDS = 120

# Legacy rows carry lower-cased statuses, so the comparison goes through the
# same helper every other status query uses rather than the enum values alone.
_TERMINAL_AGENT_RUN_STATUS_VALUES = run_status_values_for_db(
    TERMINAL_AGENT_RUN_STATUSES
)
_NON_TERMINAL_HOST_RUN_STATES = [
    state.value
    for state in AgentHostRunState
    if state not in TERMINAL_AGENT_HOST_RUN_STATES
]
# Command states that mean a CANCEL_RUN for this lease is already on its way or
# has already landed, so a sweep must not stack another one every tick.
_LIVE_COMMAND_STATES = (
    AgentHostCommandState.QUEUED.value,
    AgentHostCommandState.DELIVERED.value,
    AgentHostCommandState.ACKNOWLEDGED.value,
)


async def expire_unaccepted_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    now: datetime | None = None,
) -> AgentHostRunState | None:
    """Terminalize a run the host never durably accepted."""
    timestamp = now or utcnow()
    lease = await session.get(
        AgentHostRunLeaseModel,
        run_id,
        with_for_update=True,
    )
    if (
        lease is None
        or lease.accepted_at is not None
        or lease.lease_expires_at > timestamp
    ):
        return None
    current_state = AgentHostRunState(lease.state)
    if current_state not in {
        AgentHostRunState.QUEUED_FOR_HOST,
        AgentHostRunState.LEASED,
    }:
        return None
    terminal_state, error_code, error_detail = _unaccepted_timeout(current_state)
    lease.state = terminal_state.value
    lease.error_code = error_code
    lease.error_detail = error_detail
    lease.terminal_at = timestamp
    lease.lease_expires_at = timestamp
    lease.updated_at = timestamp
    commands = await session.execute(
        select(AgentHostCommandModel)
        .where(
            AgentHostCommandModel.run_id == run_id,
            AgentHostCommandModel.kind == AgentHostCommandKind.START_RUN.value,
            AgentHostCommandModel.state.in_(
                [
                    AgentHostCommandState.QUEUED.value,
                    AgentHostCommandState.DELIVERED.value,
                ]
            ),
        )
        .with_for_update()
    )
    for command in commands.scalars():
        command.state = AgentHostCommandState.CANCELLED.value
    await session.flush()
    return terminal_state


async def reconcile_expired_run(
    session: AsyncSession,
    *,
    run_id: UUID,
    now: datetime | None = None,
    recovery_grace_seconds: int = DEFAULT_RECOVERY_GRACE_SECONDS,
) -> AgentHostRunLeaseModel | None:
    """Advance an expired, accepted lease without risking duplicate work."""
    timestamp = now or utcnow()
    lease = await session.get(
        AgentHostRunLeaseModel,
        run_id,
        with_for_update=True,
    )
    if (
        lease is None
        or lease.lease_expires_at >= timestamp
        or AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES
        or lease.accepted_at is None
    ):
        return lease

    if AgentHostRunState(lease.state) is AgentHostRunState.RECOVERING:
        lease.state = AgentHostRunState.DISPATCH_UNKNOWN.value
        lease.error_code = "HOST_LEASE_EXPIRED"
        lease.error_detail = (
            "The Agent Host disconnected after accepting the run; "
            "Lemma did not repeat the turn because provider dispatch "
            "could not be ruled out"
        )
        lease.terminal_at = timestamp
        lease.lease_expires_at = timestamp
    else:
        lease.state = AgentHostRunState.RECOVERING.value
        lease.error_code = "HOST_RECOVERING"
        lease.error_detail = "Waiting for the Agent Host to reconnect"
        lease.lease_expires_at = timestamp + timedelta(seconds=recovery_grace_seconds)
    lease.updated_at = timestamp
    await session.flush()
    return lease


async def cancel_already_queued(
    session: AsyncSession,
    *,
    run_id: UUID,
    lease_epoch: int,
) -> bool:
    """Whether a CANCEL_RUN for this exact lease is already on its way.

    Shared by the abandoned-run sweep and by ``enqueue_cancel`` so both answer
    "is one already in flight" the same way. Fenced on ``lease_epoch``: a
    cancel queued against a superseded epoch says nothing about the run the
    host is executing now.
    """
    return bool(
        await session.scalar(
            select(
                select(AgentHostCommandModel.id)
                .where(
                    AgentHostCommandModel.run_id == run_id,
                    AgentHostCommandModel.kind == AgentHostCommandKind.CANCEL_RUN.value,
                    AgentHostCommandModel.lease_epoch == lease_epoch,
                    AgentHostCommandModel.state.in_(_LIVE_COMMAND_STATES),
                )
                .exists()
            )
        )
    )


async def cancel_abandoned_host_runs(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 100,
) -> list[UUID]:
    """Stop host runs whose Lemma run has already ended. Returns hosts to poke.

    Nothing else closes this gap. When the worker driving a run dies — OOM,
    eviction, a task timeout — the run is finalized FAILED by the shielded write
    or by ``reconcile_orphaned_agent_runs``, but the lease and the ACP agent on
    the user's machine know nothing about it. The agent keeps thinking, keeps
    calling tools against the pod, and keeps spending tokens for a turn Lemma
    has already reported as failed.

    Matching on "the agent run is terminal but its lease is not" is exact rather
    than time-based, so it catches that within one sweep of the finalization
    regardless of how long the run had been going.
    """
    timestamp = now or utcnow()
    already_cancelling = (
        select(AgentHostCommandModel.run_id)
        .where(
            AgentHostCommandModel.run_id == AgentHostRunLeaseModel.run_id,
            AgentHostCommandModel.kind == AgentHostCommandKind.CANCEL_RUN.value,
            AgentHostCommandModel.lease_epoch == AgentHostRunLeaseModel.lease_epoch,
            AgentHostCommandModel.state.in_(_LIVE_COMMAND_STATES),
        )
        .exists()
    )
    result = await session.execute(
        select(AgentHostRunLeaseModel)
        .join(AgentRunModel, AgentRunModel.id == AgentHostRunLeaseModel.run_id)
        .where(
            AgentHostRunLeaseModel.state.in_(_NON_TERMINAL_HOST_RUN_STATES),
            AgentRunModel.status.in_(_TERMINAL_AGENT_RUN_STATUS_VALUES),
            ~already_cancelling,
        )
        .order_by(AgentHostRunLeaseModel.updated_at.asc())
        .limit(limit)
        .with_for_update(of=AgentHostRunLeaseModel, skip_locked=True)
    )
    host_ids: list[UUID] = []
    for lease in result.scalars():
        session.add(
            AgentHostCommandModel(
                host_id=lease.host_id,
                run_id=lease.run_id,
                kind=AgentHostCommandKind.CANCEL_RUN.value,
                lease_epoch=lease.lease_epoch,
                payload={"agent_run_id": str(lease.run_id)},
                state=AgentHostCommandState.QUEUED.value,
                expires_at=timestamp + timedelta(seconds=DEFAULT_COMMAND_TTL_SECONDS),
            )
        )
        host_ids.append(lease.host_id)
    await session.flush()
    return host_ids


async def reconcile_expired_leases(
    session: AsyncSession,
    *,
    now: datetime | None = None,
    limit: int = 200,
) -> int:
    """Advance every lease whose heartbeat has lapsed. Returns how many moved.

    ``reconcile_expired_run`` is normally driven by the harness watching its own
    run. A worker that dies takes that watcher with it, and the lease then sits
    non-terminal forever — never swept by retention, which only collects
    terminalized rows. This is the sweep that finishes them.
    """
    timestamp = now or utcnow()
    result = await session.execute(
        select(AgentHostRunLeaseModel.run_id)
        .where(
            AgentHostRunLeaseModel.lease_expires_at < timestamp,
            AgentHostRunLeaseModel.state.in_(_NON_TERMINAL_HOST_RUN_STATES),
        )
        .order_by(AgentHostRunLeaseModel.lease_expires_at.asc())
        .limit(limit)
    )
    reconciled = 0
    for run_id in result.scalars():
        await expire_unaccepted_run(session, run_id=run_id, now=timestamp)
        await reconcile_expired_run(session, run_id=run_id, now=timestamp)
        reconciled += 1
    return reconciled


async def cleanup_retained_state(
    session: AsyncSession,
    *,
    now: datetime | None = None,
) -> None:
    """Daily sweep. Active runs are never collected."""
    timestamp = now or utcnow()
    await session.execute(
        delete(AgentHostPairingModel).where(
            AgentHostPairingModel.expires_at < timestamp - _TRANSIENT_RETENTION
        )
    )
    # One shared subquery so a clock anomaly cannot sweep an active run.
    terminal_runs = select(AgentHostRunLeaseModel.run_id).where(
        AgentHostRunLeaseModel.terminal_at.is_not(None),
        AgentHostRunLeaseModel.terminal_at < timestamp - _DISPATCH_RETENTION,
    )
    await session.execute(
        delete(AgentHostCommandModel).where(
            AgentHostCommandModel.run_id.in_(terminal_runs)
        )
    )
    await session.execute(
        delete(AgentHostRunLeaseModel).where(
            AgentHostRunLeaseModel.terminal_at.is_not(None),
            AgentHostRunLeaseModel.terminal_at < timestamp - _DISPATCH_RETENTION,
        )
    )
    await session.flush()


def _unaccepted_timeout(
    current_state: AgentHostRunState,
) -> tuple[AgentHostRunState, str, str]:
    if current_state is AgentHostRunState.QUEUED_FOR_HOST:
        return (
            AgentHostRunState.FAILED,
            "HOST_WAIT_TIMEOUT",
            "No Agent Host received the run before its wait deadline",
        )
    # Delivery is a one-way boundary: the host may have durably accepted and
    # dispatched the prompt even when its next checkpoint was lost.
    return (
        AgentHostRunState.DISPATCH_UNKNOWN,
        "HOST_ACCEPTANCE_UNKNOWN",
        "The run was delivered to Agent Host, but acceptance could not be "
        "confirmed; Lemma did not start a fallback",
    )
