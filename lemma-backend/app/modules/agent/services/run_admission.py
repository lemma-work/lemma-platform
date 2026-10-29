"""Admitting a new turn: one check for every way a turn begins.

A person's message, a retry, a workflow's step and a schedule's firing each
start a turn nobody has admitted yet: the runtime it resolved must be one this
person may run and the provider still serves, and the account must have
headroom. Workflow and schedule turns skipped both, so an exhausted account's
automation kept spending and a retired model failed inside the run instead of
at its start.

A run that continues an admitted turn -- a follow-up, a resume -- asks only
for headroom (``usage_preflight``): its runtime was admitted with the turn.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.services.runtime_availability import (
    TurnRuntimeCheck,
    ensure_turn_runtime_available,
)
from app.modules.agent.services.usage_preflight import assert_turn_allowed
from app.modules.usage.contracts.execution import UsageService


async def admit_turn(
    uow: SqlAlchemyUnitOfWork,
    conversation: Conversation,
    runtime: AgentRuntimeConfig,
    *,
    user_id: UUID,
    usage_service: UsageService | None,
    runtime_check: TurnRuntimeCheck = ensure_turn_runtime_available,
) -> None:
    """Raise when a new turn of ``conversation`` on ``runtime`` must not start."""
    await runtime_check(
        uow,
        runtime,
        organization_id=conversation.organization_id,
        user_id=user_id,
    )
    await assert_turn_allowed(
        usage_service, conversation, user_id=user_id, agent_runtime=runtime
    )


__all__ = ["admit_turn"]
