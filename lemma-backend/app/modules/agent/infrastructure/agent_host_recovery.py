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
from app.modules.agent.infrastructure.agent_host_repository_common import utcnow
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
