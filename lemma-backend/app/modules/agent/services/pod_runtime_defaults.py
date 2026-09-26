"""The runtime a pod falls back to when nothing more specific is configured.

Read from the pod's config on every path that starts a run without an explicit
runtime: a new turn, and an approved tool re-executed as the user. Both need the
same answer or an approved tool would run against a different model than the run
that asked for it.
"""

from __future__ import annotations

from uuid import UUID

from app.modules.pod.contracts.agent_access import pod_config, pod_organization_id
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
)
from app.modules.agent.services import runtime_system_profiles
from app.modules.agent.services.workspace_model_fallback import (
    organization_default_runtime,
)
from app.modules.pod.contracts import PodConfig


async def default_agent_runtime_for_pod(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
) -> AgentRuntimeConfig:
    """The pod's configured default runtime, or the organization's, or the
    system one.

    The organization step exists for a deployment with no model of its own --
    Desktop, or any self-host set up through Settings -> Models. Adding a
    provider there has to be enough for a teammate to answer; before this, a
    pod nobody had pinned went straight to the absent system model and every
    message failed with "no model is set up" beside a provider that was.
    """
    config = await pod_config(uow, pod_id)
    runtime = PodConfig.from_raw(config).resolved_default_runtime()
    if runtime is not None:
        return runtime
    # Read through the module so a test can arrange "no system model" without
    # reaching into this one.
    if not runtime_system_profiles.system_profile_configured():
        organization_id = await pod_organization_id(uow, pod_id)
        if organization_id is not None:
            organization_runtime = await organization_default_runtime(
                uow, organization_id=organization_id
            )
            if organization_runtime is not None:
                return organization_runtime
    return AgentRuntimeConfig(profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID)
