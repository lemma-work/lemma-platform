"""Where a background model call runs when the deployment has no model of its own.

Titles, the vision delegate, schedule filters and README polish each ask for
the *system* model -- `system:lemma`, the provider the deployment's environment
supplies. A deployment is allowed not to have one: a Desktop install is set up
by adding a provider on Organization -> Models, and a self-host can do the same
without ever touching the environment. Chat still works there, because a pod
names its own runtime; these callers used to fail outright, because they only
knew the one name.

So: the system model when there is one -- unchanged, byte for byte -- and
otherwise the model the workspace already runs on. The pod's default runtime
when a pod is in view, because that is the model its owner picked; then the
organization's providers, organization-wide before personal, in the order the
Models page lists them. Only model providers qualify: a coding agent on
somebody's computer is not something a one-shot title prompt can be sent to.

Nothing here knows a vendor. A model is chosen by what its catalog entry
declares, which is all the backend knows about any provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from app.core.crypto import get_secret_cipher
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import create_uow_from_session_maker
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileScope,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.infrastructure.agent_host.repository import AgentHostRepository
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.services.runtime_profile_service import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
    AgentRuntimeProfileService,
    ResolvedAgentRuntime,
)
from app.modules.agent.services.runtime_system_profiles import (
    is_model_not_configured,
    model_not_configured_error,
)
from app.modules.pod.contracts import PodConfig
from app.modules.pod.contracts.agent_access import pod_config


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A workspace profile, and the model on it the caller would have picked."""

    profile: AgentRuntimeProfile
    preferred_model_name: str | None


class WorkspaceRuntimeResolver(Protocol):
    """`resolve_workspace_runtime`'s shape, for callers that take it injected."""

    async def __call__(
        self,
        *,
        organization_id: UUID | None,
        user_id: UUID,
        model_name: str | None = None,
        pod_id: UUID | None = None,
        require_vision: bool = False,
    ) -> ResolvedAgentRuntime | None: ...


async def resolve_system_or_workspace_runtime(
    *,
    organization_id: UUID | None,
    user_id: UUID,
    model_name: str | None = None,
    pod_id: UUID | None = None,
    workspace_runtime: WorkspaceRuntimeResolver | None = None,
) -> ResolvedAgentRuntime:
    """The system model, or the workspace's when the deployment has none.

    ``model_name`` is the caller's configured choice; on the system profile it
    resolves exactly as it always did. Raises the ``model_not_configured``
    `DomainError` when neither exists, so every caller reports the same,
    fixable, thing.
    """
    try:
        return await AgentRuntimeProfileService().resolve(
            runtime=AgentRuntimeConfig(
                profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
                model_name=model_name,
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
    except DomainError as error:
        if not is_model_not_configured(error):
            raise
    resolved = await (workspace_runtime or resolve_workspace_runtime)(
        organization_id=organization_id,
        user_id=user_id,
        model_name=model_name,
        pod_id=pod_id,
    )
    if resolved is None:
        raise model_not_configured_error()
    return resolved


async def resolve_workspace_runtime(
    *,
    organization_id: UUID | None,
    user_id: UUID,
    model_name: str | None = None,
    pod_id: UUID | None = None,
    require_vision: bool = False,
) -> ResolvedAgentRuntime | None:
    """A model the workspace already runs on, or ``None`` when it has none.

    ``model_name`` is honoured when a candidate profile serves it; otherwise
    that profile's own default stands in, because a name configured for the
    system provider means nothing to another one.

    ``require_vision`` narrows the search to models that declare they read
    images. It never settles for one that does not: handing image content to a
    text-only model is the provider 400 the vision delegate exists to avoid.
    """
    if organization_id is None:
        return None
    # Only reads happen inside the session. The model call the caller makes
    # with the result happens after the connection is back in the pool.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        service = AgentRuntimeProfileService(
            repository=AgentRuntimeProfileRepository(
                uow, encryption=get_secret_cipher()
            ),
            host_repository=AgentHostRepository(uow),
        )
        pod_default = (
            PodConfig.from_raw(await pod_config(uow, pod_id)).resolved_default_runtime()
            if pod_id is not None
            else None
        )
        profiles = await service.list_profiles(
            organization_id=organization_id, user_id=user_id
        )
        runtime = choose_workspace_runtime(
            profiles,
            pod_default=pod_default,
            model_name=model_name,
            require_vision=require_vision,
        )
        if runtime is None:
            return None
        return await service.resolve(
            runtime=runtime,
            organization_id=organization_id,
            user_id=user_id,
        )


def choose_workspace_runtime(
    profiles: list[AgentRuntimeProfile],
    *,
    pod_default: AgentRuntimeConfig | None,
    model_name: str | None,
    require_vision: bool,
) -> AgentRuntimeConfig | None:
    """Pick the workspace model a background call should run on, or ``None``.

    Pure, so the ordering is testable without a database: pod default first,
    then organization-wide providers, then the caller's personal ones.
    """
    for candidate in _candidates(profiles, pod_default=pod_default):
        entry = _pick_entry(
            candidate.profile,
            names=(model_name, candidate.preferred_model_name),
            require_vision=require_vision,
        )
        if entry is not None:
            return AgentRuntimeConfig(
                profile_id=candidate.profile.id, model_name=entry.name
            )
    return None


def _candidates(
    profiles: list[AgentRuntimeProfile],
    *,
    pod_default: AgentRuntimeConfig | None,
) -> list[_Candidate]:
    providers = [
        profile
        for profile in profiles
        if profile.kind is RuntimeProfileKind.MODEL_PROVIDER
        and profile.scope is not RuntimeProfileScope.SYSTEM
        and profile.model_catalog
    ]
    ordered: list[_Candidate] = []
    if pod_default is not None:
        ordered.extend(
            _Candidate(
                profile=profile,
                preferred_model_name=pod_default.model_name
                or profile.default_model_name,
            )
            for profile in providers
            if profile.id == pod_default.profile_id
        )
    for scope in (RuntimeProfileScope.ORGANIZATION, RuntimeProfileScope.PERSONAL):
        ordered.extend(
            _Candidate(profile=profile, preferred_model_name=profile.default_model_name)
            for profile in providers
            if profile.scope is scope
        )
    return ordered


def _pick_entry(
    profile: AgentRuntimeProfile,
    *,
    names: tuple[str | None, ...],
    require_vision: bool,
) -> RuntimeModelCatalogEntry | None:
    eligible = [
        entry
        for entry in profile.model_catalog
        if not require_vision or RuntimeModelCapability.VISION in entry.capabilities
    ]
    for name in names:
        for entry in eligible:
            if name and entry.name == name:
                return entry
    return eligible[0] if eligible else None


__all__ = [
    "WorkspaceRuntimeResolver",
    "choose_workspace_runtime",
    "resolve_system_or_workspace_runtime",
    "resolve_workspace_runtime",
]
