"""The model a call's coordinator runs on, when the deployment pins one.

A coordinator's turns are frequent and mostly short -- read the turn, delegate,
relay -- so a fast model fits it better than whatever the agent defaults to.
Its sub-agents inherit the call conversation's runtime, so the pin covers them.
"""

from __future__ import annotations

from uuid import UUID

from pydantic_ai import UsageLimits

from app.core.domain.runtime import AgentRuntimeConfig
from app.modules.agent.contracts.model_runtime import resolve_system_runtime


async def coordinator_runtime(
    model_name: str | None, *, user_id: UUID, pod_id: UUID
) -> AgentRuntimeConfig | None:
    """The pinned runtime, or None to run on the agent's own."""
    if not model_name:
        return None
    resolved = await resolve_system_runtime(
        usage_limits=UsageLimits(),
        user_id=user_id,
        pod_id=pod_id,
        model_name=model_name,
    )
    profile = resolved.runtime_profile
    profile_id = profile.get("profile_id")
    if not isinstance(profile_id, str) or not profile_id:
        return None
    model = profile.get("model_name")
    return AgentRuntimeConfig(
        profile_id=profile_id,
        model_name=model if isinstance(model, str) and model else None,
    )
