"""The runtime a run answering somebody outside the pod may use.

Such a run authorizes as nobody inside Lemma's own tools (see
``domain/outsiders``). A run on Agent Host is not inside them: the coding tool
there keeps a shell of its own, and its Lemma token is minted for the
conversation's user -- on this run, the member who answers for the group. The
anonymous context and the outsider toolset govern Lemma's tools, and a shell
command reaches neither, so a stranger who asked the agent to print its
environment would be handed that member's credential.

So an outsider's run only ever runs in this process (``HarnessKind.LEMMA``): on
the runtime it was given when that is one, else on the organization's default
model, else the system's -- and when none of those runs here, not at all.
Chosen at dispatch, the one place every run passes through with its runtime
resolved, whichever path created it.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from uuid import UUID

from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.agent.domain.outsiders import OutsiderRunRefused
from app.modules.agent.domain.runtime_profiles import RuntimeProfileScope
from app.modules.agent.domain.value_objects import AgentRuntimeConfig, HarnessKind
from app.modules.agent.services import runtime_system_profiles
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
    ResolvedAgentRuntime,
)
from app.modules.agent.services.workspace_model_fallback import (
    organization_default_runtime,
)

ResolveRuntime = Callable[[AgentRuntimeConfig], Awaitable[ResolvedAgentRuntime]]

_NO_IN_PROCESS_MODEL = (
    "This teammate cannot answer people outside the space: that needs a model "
    "the organization provides, not a coding agent or someone's personal key. "
    "Choose one for the organization in Settings → Models."
)


async def in_process_runtime(
    resolved: ResolvedAgentRuntime,
    *,
    fallbacks: Sequence[AgentRuntimeConfig],
    resolve: ResolveRuntime,
) -> ResolvedAgentRuntime:
    """``resolved`` if a stranger's run may use it, else the first fallback that may.

    It must run here (``HarnessKind.LEMMA``), and it must not be somebody's
    personal model: a member's own key, billed to them, is not the pod's to
    spend on people outside it.
    """
    if _usable(resolved):
        return resolved
    for config in fallbacks:
        try:
            candidate = await resolve(config)
        except DomainError, RuntimeError:
            # Not configured, retired or unavailable: the next one may be.
            continue
        if _usable(candidate):
            return candidate
    raise OutsiderRunRefused(_NO_IN_PROCESS_MODEL)


def _usable(candidate: ResolvedAgentRuntime) -> bool:
    return (
        candidate.harness_kind is HarnessKind.LEMMA
        and candidate.profile.scope is not RuntimeProfileScope.PERSONAL
    )


async def default_runtimes(
    uow_factory: UnitOfWorkFactory, organization_id: UUID | None
) -> list[AgentRuntimeConfig]:
    """The organization's default model, then the system's, in that order."""
    defaults: list[AgentRuntimeConfig] = []
    if organization_id is not None:
        async with uow_factory() as uow:
            chosen = await organization_default_runtime(
                uow,
                organization_id=organization_id,
                server_has_model=runtime_system_profiles.system_profile_configured(),
            )
        if chosen is not None:
            defaults.append(chosen)
    defaults.append(
        AgentRuntimeConfig(profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID)
    )
    return defaults
