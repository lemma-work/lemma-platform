"""Running one thing on the system model, from outside `agent`.

An operation, not the four names behind it. Two callers needed the system model
-- `schedule`'s event filter and `pod_bundle`'s README polish -- and each
assembled it itself: resolve the default system profile, take its public
snapshot, take its credentials, take its harness model name, hand all three to
the model factory, then remember to run its own usage limits through
`usage_limits_for`. Five lines and four names, duplicated, in two modules that
have no business knowing a model is a thing built out of a runtime profile. That
knowledge is what `app/composition/{schedule_filter,pod_bundle_readme}.py` were:
moving `usage_limits_for` between two agent service modules broke a README
generator. `AgentRuntimeProfileService`, `default_system_runtime` and
`require_pydantic_ai_model_from_runtime_profile` stay unpublished; this is the
call that replaces them.

The ceiling is the caller's, which is why it is an argument rather than a
constant here: the filter and the polish budget different amounts. What comes
back is not necessarily what went in. A model that cannot pre-count tokens
enforces a cap only *after* the provider has been called and billed, so
`usage_limits_for` drops `count_tokens_before_request` rather than raising --
and `usage_limits` below is what this model will genuinely honour. A caller
whose input has no upper size still has to bound it itself; `_MAX_EVENT_CHARS`
in schedule's filter is that bound, and it exists because of exactly this.

A submodule rather than `contracts/__init__.py`, which is a leaf: this reaches
the runtime profile service, and everything importing any agent contract would
otherwise pay for it. Same reason as `workflow_control.py` next door.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from pydantic_ai import UsageLimits
from pydantic_ai.models import Model

from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.services.runtime_model_factory import (
    require_pydantic_ai_model_from_runtime_profile,
    usage_limits_for,
)
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
    AgentRuntimeProfileService,
)


@dataclass(frozen=True, slots=True)
class SystemModelRuntime:
    """One resolved system model, plus the two things a run needs beside it."""

    #: Hand this to `pydantic_ai.Agent`.
    model: Model
    #: The profile snapshot `usage` reserves and records a run against.
    runtime_profile: dict[str, object | None]
    #: The caller's ceiling, reduced to what this model can actually enforce.
    usage_limits: UsageLimits


async def resolve_system_runtime(
    *,
    usage_limits: UsageLimits,
    user_id: UUID | None = None,
    organization_id: UUID | None = None,
) -> SystemModelRuntime:
    """The system model, ready to run under `usage_limits`.

    `user_id` and `organization_id` scope the profile lookup. A caller running
    on nobody's behalf may omit them -- the default system profile is
    code-defined and belongs to no user -- and gets the same profile a
    per-user resolution would find when the workspace has not overridden it.
    """
    resolved = await AgentRuntimeProfileService().resolve(
        runtime=AgentRuntimeConfig(profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID),
        organization_id=organization_id,
        user_id=user_id or uuid4(),
    )
    runtime_profile = resolved.public_snapshot()
    model = require_pydantic_ai_model_from_runtime_profile(
        runtime_profile=runtime_profile,
        runtime_credentials=resolved.credentials or {},
        fallback_model_name=resolved.model_name_for_harness,
    )
    return SystemModelRuntime(
        model=model,
        runtime_profile=runtime_profile,
        usage_limits=usage_limits_for(model, usage_limits),
    )


__all__ = ["SystemModelRuntime", "resolve_system_runtime"]
