"""The runtime a pod falls back to when nothing more specific is configured.

Read from the pod's config on every path that starts a run without an explicit
runtime: a new turn, and an approved tool re-executed as the user. Both need the
same answer or an approved tool would run against a different model than the run
that asked for it.
"""

from __future__ import annotations

from uuid import UUID

from app.composition.agent_pod import create_agent_pod_repository
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
)
from app.modules.pod.contracts import PodConfig


async def default_agent_runtime_for_pod(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
) -> AgentRuntimeConfig:
    """The pod's configured default runtime, or the system one."""
    config = await create_agent_pod_repository(uow).get_config(pod_id)
    runtime = PodConfig.from_raw(config).resolved_default_runtime()
    return runtime or AgentRuntimeConfig(
        profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID
    )
