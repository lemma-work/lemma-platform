"""Lease timeout and recovery transitions for Agent Host dispatch."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select

from app.modules.agent.domain.agent_host import (
    TERMINAL_AGENT_HOST_RUN_STATES,
    AgentHostCheckpoint,
    AgentHostCommandKind,
    AgentHostCommandState,
    AgentHostRunState,
)
from app.modules.agent.infrastructure.agent_host_repository_common import utcnow
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostCommandModel,
    AgentHostRunLeaseModel,
)


class AgentHostRecoveryRepositoryMixin:
    session: Any

    async def expire_unaccepted_run(
        self,
        *,
        run_id: UUID,
        now: datetime | None = None,
    ) -> AgentHostRunState | None:
        timestamp = now or utcnow()
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            run_id,
            with_for_update=True,
        )
        if (
            lease is None
            or lease.checkpoint is not None
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
        lease.checkpoint = AgentHostCheckpoint.TERMINAL.value
        lease.error_code = error_code
        lease.error_detail = error_detail
        lease.terminal_at = timestamp
        lease.lease_expires_at = timestamp
        lease.updated_at = timestamp
        commands = await self.session.execute(
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
        await self.session.flush()
        return terminal_state

    async def reconcile_expired_run(
        self,
        *,
        run_id: UUID,
        now: datetime | None = None,
        recovery_grace_seconds: int = 120,
    ) -> AgentHostRunLeaseModel | None:
        """Advance an expired, accepted lease without risking duplicate work."""
        timestamp = now or utcnow()
        lease = await self.session.get(
            AgentHostRunLeaseModel,
            run_id,
            with_for_update=True,
        )
        if (
            lease is None
            or lease.lease_expires_at >= timestamp
            or AgentHostRunState(lease.state) in TERMINAL_AGENT_HOST_RUN_STATES
            or lease.checkpoint is None
        ):
            return lease

        if AgentHostRunState(lease.state) is AgentHostRunState.RECOVERING:
            lease.state = AgentHostRunState.DISPATCH_UNKNOWN.value
            lease.checkpoint = AgentHostCheckpoint.TERMINAL.value
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
            lease.checkpoint = AgentHostCheckpoint.RECOVERING.value
            lease.error_code = "HOST_RECOVERING"
            lease.error_detail = "Waiting for the Agent Host to reconnect"
            lease.lease_expires_at = timestamp + timedelta(
                seconds=recovery_grace_seconds
            )
        lease.updated_at = timestamp
        await self.session.flush()
        return lease


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
