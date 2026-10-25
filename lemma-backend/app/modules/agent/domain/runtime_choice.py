"""Which runtime a run gets, as pure steps over what the reads returned.

Each step of the one resolution chain (``services/runtime_resolution``) that
decides without I/O lives here, so the ordering is testable without a database:
the organization's default, the workspace model a background call falls back
to, and whether a runtime may answer somebody outside the pod.

Nothing here knows a vendor. A model is chosen by what its catalog entry
declares, which is all the backend knows about any provider.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from app.modules.agent.domain.organization_default import organization_default_of
from app.modules.agent.domain.resolved_runtime import ResolvedAgentRuntime
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileScope,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig, HarnessKind


class RuntimePurpose(StrEnum):
    """What a runtime is being resolved for; each is one path through the chain."""

    #: A turn: the run's own runtime, held to the outsider rules.
    TURN = "turn"
    #: A conversation title: the system model, else the workspace's.
    TITLE = "title"
    #: The memory pass's configured model, else the run's own.
    MEMORY = "memory_pass"
    #: The vision delegate: only a model that reads images.
    VISION = "vision"
    #: History compaction's configured model, else the run's own.
    COMPACTION = "summarization"


@dataclass(frozen=True, slots=True)
class _Candidate:
    """A workspace profile, and the model on it the caller would have picked."""

    profile: AgentRuntimeProfile
    preferred_model_name: str | None


def choose_organization_runtime(
    profiles: list[AgentRuntimeProfile],
    *,
    server_has_model: bool = False,
) -> AgentRuntimeConfig | None:
    """The organization's chosen model, else -- only when the deployment has no
    model of its own -- the first organization-wide provider on its default.

    The chosen model wins over the system one because an owner picked it on
    purpose; the first-provider guess does not, because nobody picked it and a
    deployment that ships a model meant that one.

    Pure, and shared by run routing and the profile listing's
    ``default_runtime``, so "Organization default -- X" in the picker names the
    model a run will actually get.
    """
    chosen = organization_default_of(profiles)
    if chosen is not None or server_has_model:
        return chosen
    return choose_workspace_runtime(
        [
            profile
            for profile in profiles
            if profile.scope is RuntimeProfileScope.ORGANIZATION
        ],
        pod_default=None,
        model_name=None,
        require_vision=False,
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
    then the organization's chosen model, then organization-wide providers,
    then the caller's personal ones.
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
    organization_default = organization_default_of(providers)
    if organization_default is not None:
        ordered.extend(
            _Candidate(
                profile=profile,
                preferred_model_name=organization_default.model_name,
            )
            for profile in providers
            if profile.id == organization_default.profile_id
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


def runs_for_outsiders(candidate: ResolvedAgentRuntime) -> bool:
    """Whether a run answering somebody outside the pod may use ``candidate``.

    It must run here (``HarnessKind.LEMMA``), and it must not be somebody's
    personal model: a member's own key, billed to them, is not the pod's to
    spend on people outside it.
    """
    return (
        candidate.harness_kind is HarnessKind.LEMMA
        and candidate.profile.scope is not RuntimeProfileScope.PERSONAL
    )
