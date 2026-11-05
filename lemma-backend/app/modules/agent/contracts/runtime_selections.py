"""Checking a pod default runtime's coding-agent selections, for the pod module.

The pod owns ``config.default_runtime``; what an Agent Host harness offers is
the agent module's to know. Published here so a pod default carrying a
selection the harness never offered is refused when it is saved, with the same
error a teammate's runtime gets, rather than dropped at every run.

A submodule rather than ``contracts/__init__``: this reaches the service layer.
"""

from __future__ import annotations

from uuid import UUID

from app.core.domain.runtime import AgentRuntimeConfig
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.services.runtime_profile_service import (
    runtime_profile_service_for,
)
from app.modules.agent.services.runtime_selection_check import (
    check_runtime_config_selections,
)


async def check_pod_default_runtime_selections(
    uow: SqlAlchemyUnitOfWork,
    runtime: AgentRuntimeConfig | None,
    *,
    organization_id: UUID,
    user_id: UUID,
) -> None:
    """Refuse a pod default whose selections its harness does not offer."""
    if runtime is None or not runtime.config_selections:
        return
    await check_runtime_config_selections(
        runtime,
        profiles=runtime_profile_service_for(uow),
        organization_id=organization_id,
        user_id=user_id,
    )


__all__ = ["check_pod_default_runtime_selections"]
