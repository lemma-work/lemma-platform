"""Which runtime a run, or a background model call, gets: one ordered chain.

Every path that needs a model resolves it here, by purpose
(``RuntimePurpose``): a turn, a conversation title, the memory pass, the vision
delegate and history compaction. Each source in the chain is a small step --
the pure ones in ``domain/runtime_choice`` -- and each read it needs is made
once:

* **the run's own runtime**: the conversation's or teammate's pin, else the
  pod's default, else the organization's chosen model, else the system's
  (``default_agent_runtime_for_pod``). The organization's chosen model wins
  over the system model: an owner picked it for every teammate. Without one, a
  deployment with no model of its own -- Desktop, or any self-host set up
  through Settings -> Models -- falls to its first organization provider.
* **a stranger's turn** only ever runs in this process (``HarnessKind.LEMMA``)
  and never on a member's personal key: on the runtime it was given when that
  is one, else the organization's default, else the system's, and when none of
  those runs here, not at all (``in_process_runtime``). A run on Agent Host
  gives a coding tool its own shell and a Lemma token minted for the member who
  answers for the group; a shell command reaches neither the anonymous context
  nor the outsider toolset.
* **a background call** (title, vision) asks for the *system* model, and when
  the deployment has none, for the model the workspace already runs on: the
  pod's default, then the organization's providers, organization-wide before
  personal (``resolve_system_or_workspace_runtime``). Compaction and the memory
  pass name a cheaper system model of their own, and fall back to the run's.
* **the system default** a local checkout may point elsewhere with a file
  (``local_default_runtime``).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from pydantic import ValidationError

from app.core.config import settings
from app.core.crypto import get_secret_cipher
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import (
    SessionUnitOfWorkFactory,
    UnitOfWorkFactory,
    create_uow_from_session_maker,
)
from app.core.log.log import get_logger
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.errors import RuntimeUnavailableError
from app.modules.agent.domain.organization_default import ORGANIZATION_WIDE_VIEWER
from app.modules.agent.domain.outsiders import OutsiderRunRefused
from app.modules.agent.domain.resolved_runtime import (
    ResolvedAgentRuntime,
)
from app.modules.agent.domain.runtime_choice import (
    RuntimePurpose,
    choose_organization_runtime,
    choose_workspace_runtime,
    runs_for_outsiders,
)
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCapability,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig
from app.modules.agent.domain.vision import VisionUnavailableError
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.services import runtime_system_profiles
from app.modules.agent.services.runtime_system_profiles import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
    is_model_not_configured,
    model_not_configured_error,
)
from app.modules.pod.contracts import PodConfig
from app.modules.pod.contracts.agent_access import pod_config, pod_organization_id

logger = get_logger(__name__)

#: Resolves one configured runtime; the caller decides over which unit of work.
ResolveConfig = Callable[[AgentRuntimeConfig], Awaitable[ResolvedAgentRuntime]]

_NO_IN_PROCESS_MODEL = (
    "This teammate cannot answer people outside the space: that needs a model "
    "the organization provides, not a coding agent or someone's personal key. "
    "Choose one for the organization in Settings → Models."
)
_NO_IMAGE_MODEL = "No model that reads images is set up for this deployment."


@dataclass(frozen=True, slots=True)
class RuntimeRequest:
    """Who a runtime is resolved for, and what they configured."""

    organization_id: UUID | None
    user_id: UUID
    pod_id: UUID | None = None
    #: The run's own runtime (a turn): the conversation's or teammate's pin.
    configured: AgentRuntimeConfig | None = None
    #: A turn answering somebody outside the pod.
    answers_outsiders: bool = False
    #: The model a background purpose is configured to use, if any.
    model_name: str | None = None


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


class ProfileResolver(Protocol):
    """The part of ``AgentRuntimeProfileService`` the chain calls."""

    async def resolve(
        self,
        *,
        runtime: AgentRuntimeConfig | None,
        organization_id: UUID | None,
        user_id: UUID,
    ) -> ResolvedAgentRuntime: ...


async def resolve_runtime(
    request: RuntimeRequest,
    *,
    purpose: RuntimePurpose,
    resolve: ResolveConfig | None = None,
    uow_factory: UnitOfWorkFactory | None = None,
    profiles: Callable[[], ProfileResolver] | None = None,
    workspace_runtime: WorkspaceRuntimeResolver | None = None,
) -> ResolvedAgentRuntime | None:
    """The runtime ``purpose`` runs on for ``request``.

    ``None`` only for the purposes that fall back to the run's own model
    (compaction, the memory pass) when their configured system model is unset
    or cannot be had. ``resolve``, ``uow_factory``, ``profiles`` and
    ``workspace_runtime`` replace the reads, for a caller that owns its unit of
    work or a test that owns the reads.
    """
    system = profiles or _system_profiles
    if purpose is RuntimePurpose.TURN:
        return await _turn_runtime(
            request,
            resolve=resolve or _resolver(request, uow_factory),
            uow_factory=uow_factory,
        )
    if purpose is RuntimePurpose.TITLE:
        return await resolve_system_or_workspace_runtime(
            organization_id=request.organization_id,
            user_id=request.user_id,
            model_name=request.model_name,
            pod_id=request.pod_id,
            workspace_runtime=workspace_runtime,
            profiles=system,
            default_when_unavailable=True,
        )
    if purpose is RuntimePurpose.VISION:
        return await _vision_runtime(request, profiles=system)
    return await _configured_system_runtime(request, purpose=purpose, profiles=system)


async def _turn_runtime(
    request: RuntimeRequest,
    *,
    resolve: ResolveConfig,
    uow_factory: UnitOfWorkFactory | None,
) -> ResolvedAgentRuntime:
    """The run's runtime -- one that runs in this process, for a stranger's turn."""
    configured = request.configured or AgentRuntimeConfig(
        profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID
    )
    resolved = await resolve(configured)
    if not request.answers_outsiders:
        return resolved
    return await in_process_runtime(
        resolved,
        fallbacks=await default_runtimes(
            uow_factory or _session_units(), request.organization_id
        ),
        resolve=resolve,
    )


def _resolver(
    request: RuntimeRequest, uow_factory: UnitOfWorkFactory | None
) -> ResolveConfig:
    from app.modules.agent.services.runtime_profile_service import (
        runtime_profile_service_for,
    )

    async def resolve(config: AgentRuntimeConfig) -> ResolvedAgentRuntime:
        async with (uow_factory or _session_units())() as uow:
            return await runtime_profile_service_for(uow).resolve(
                runtime=config,
                organization_id=request.organization_id,
                user_id=request.user_id,
            )

    return resolve


def _session_units() -> UnitOfWorkFactory:
    return SessionUnitOfWorkFactory(async_session_maker)


def _system_profiles() -> ProfileResolver:
    from app.modules.agent.services.runtime_profile_service import (
        AgentRuntimeProfileService,
    )

    return AgentRuntimeProfileService()


# -- The system default, and the pod's and organization's defaults ------------


def local_default_runtime(
    *, environment: str | None = None, config_path: Path | str | None = None
) -> AgentRuntimeConfig:
    """The system default runtime; a local checkout may name another in a file."""
    system = AgentRuntimeConfig(profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID)
    if (environment or settings.environment) != "local":
        return system
    path = Path(
        config_path or agent_settings.local_agent_runtime_config_path
    ).expanduser()
    if not path.exists():
        return system
    try:
        return AgentRuntimeConfig.model_validate_json(path.read_text(encoding="utf-8"))
    except OSError, ValidationError, ValueError:
        return system


async def default_agent_runtime_for_pod(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
) -> AgentRuntimeConfig:
    """The pod's configured default runtime, or the organization's, or the
    system one.

    Read on every path that starts a run without an explicit runtime: a new
    turn, and an approved tool re-executed as the user. Both need the same
    answer or an approved tool would run against a different model than the
    run that asked for it.
    """
    runtime = await pod_default_runtime(uow, pod_id=pod_id)
    if runtime is not None:
        return runtime
    return await organization_or_system_runtime(uow, pod_id=pod_id)


async def pod_default_runtime(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
) -> AgentRuntimeConfig | None:
    """The runtime the pod itself names, or ``None`` when it names none."""
    return PodConfig.from_raw(await pod_config(uow, pod_id)).resolved_default_runtime()


async def organization_or_system_runtime(
    uow: SqlAlchemyUnitOfWork, *, pod_id: UUID
) -> AgentRuntimeConfig:
    """What a pod with no default of its own falls back to."""
    organization_id = await pod_organization_id(uow, pod_id)
    if organization_id is not None:
        organization_runtime = await organization_default_runtime(
            uow,
            organization_id=organization_id,
            # Read through the module so a test can arrange "no system model"
            # without reaching into this one.
            server_has_model=runtime_system_profiles.system_profile_configured(),
        )
        if organization_runtime is not None:
            return organization_runtime
    return AgentRuntimeConfig(profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID)


async def organization_default_runtime(
    uow: SqlAlchemyUnitOfWork,
    *,
    organization_id: UUID,
    server_has_model: bool = False,
) -> AgentRuntimeConfig | None:
    """What a teammate runs on when neither it nor its pod names a model, read
    on the caller's unit of work. ``None`` means the system model.

    Organization-wide providers only. A run in a shared pod must not land on one
    member's personal key because that member happened to add it.
    """
    repository = AgentRuntimeProfileRepository(uow, encryption=get_secret_cipher())
    profiles = await repository.get_visible(
        organization_id=organization_id, user_id=ORGANIZATION_WIDE_VIEWER
    )
    return choose_organization_runtime(profiles, server_has_model=server_has_model)


# -- A stranger's turn ----------------------------------------------------------


async def in_process_runtime(
    resolved: ResolvedAgentRuntime,
    *,
    fallbacks: Sequence[AgentRuntimeConfig],
    resolve: ResolveConfig,
) -> ResolvedAgentRuntime:
    """``resolved`` if a stranger's run may use it, else the first fallback that may."""
    if runs_for_outsiders(resolved):
        return resolved
    for config in fallbacks:
        try:
            candidate = await resolve(config)
        except (DomainError, RuntimeError) as exc:
            # Not configured, retired or unavailable: the next one may be. Said
            # per fallback, because when they all fail the person is told only
            # that there is no model, and this is the one record of why.
            logger.warning(
                "agent.outsider_runtime.fallback_skipped.degraded",
                profile_id=config.profile_id,
                error_code=(
                    exc.code if isinstance(exc, DomainError) else type(exc).__name__
                ),
            )
            continue
        if runs_for_outsiders(candidate):
            return candidate
    raise OutsiderRunRefused(_NO_IN_PROCESS_MODEL)


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


# -- Background calls: the system model, else the workspace's ------------------


async def resolve_system_or_workspace_runtime(
    *,
    organization_id: UUID | None,
    user_id: UUID,
    model_name: str | None = None,
    pod_id: UUID | None = None,
    workspace_runtime: WorkspaceRuntimeResolver | None = None,
    profiles: Callable[[], ProfileResolver] | None = None,
    default_when_unavailable: bool = False,
) -> ResolvedAgentRuntime:
    """The system model, or the workspace's when the deployment has none.

    ``model_name`` is the caller's configured choice; on the system profile it
    resolves exactly as it always did. With ``default_when_unavailable`` a name
    the system profile does not offer falls back to the profile's default --
    a title on the default model is still a title. Raises the
    ``model_not_configured`` `DomainError` when neither exists, so every caller
    reports the same, fixable, thing.
    """
    service = (profiles or _system_profiles)()
    try:
        return await _system_runtime(
            service,
            organization_id=organization_id,
            user_id=user_id,
            model_name=model_name,
            default_when_unavailable=default_when_unavailable,
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


async def _system_runtime(
    service: ProfileResolver,
    *,
    organization_id: UUID | None,
    user_id: UUID,
    model_name: str | None,
    default_when_unavailable: bool,
) -> ResolvedAgentRuntime:
    try:
        return await service.resolve(
            runtime=AgentRuntimeConfig(
                profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
                model_name=model_name,
            ),
            organization_id=organization_id,
            user_id=user_id,
        )
    except RuntimeUnavailableError:
        if not default_when_unavailable:
            raise
        # The configured model is not in this deployment's catalog. A
        # documented fallback for a background helper, not a teammate's pinned
        # model.
        return await service.resolve(
            runtime=AgentRuntimeConfig(
                profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID
            ),
            organization_id=organization_id,
            user_id=user_id,
        )


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
    system provider means nothing to another one. ``require_vision`` narrows
    the search to models that declare they read images, and never settles for
    one that does not.
    """
    from app.modules.agent.services.runtime_profile_service import (
        runtime_profile_service_for,
    )

    if organization_id is None:
        return None
    # Only reads happen inside the session. The model call the caller makes
    # with the result happens after the connection is back in the pool.
    async with create_uow_from_session_maker(async_session_maker) as uow:
        service = runtime_profile_service_for(uow)
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


async def _configured_system_runtime(
    request: RuntimeRequest,
    *,
    purpose: RuntimePurpose,
    profiles: Callable[[], ProfileResolver],
) -> ResolvedAgentRuntime | None:
    """``request.model_name`` on the system provider, or ``None`` when it is
    unset, there is no system provider, or it cannot be resolved (logged, with
    ``purpose`` naming the setting's job).

    For the side jobs a deployment may move off the run's own model --
    compaction, the memory pass -- each named by its own setting.
    """
    if not request.model_name:
        return None
    if not runtime_system_profiles.system_profile_configured():
        # The setting names a model on the system provider, and there is none.
        # The run's own model is then the workspace's model, so it is the
        # fallback, not a failure worth a warning on every compaction.
        return None
    try:
        return await profiles().resolve(
            runtime=AgentRuntimeConfig(
                profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
                model_name=request.model_name,
            ),
            organization_id=request.organization_id,
            user_id=request.user_id,
        )
    except Exception:
        # Warning, not debug. Falling back is silent but not free: compaction
        # then runs on the run's own model, the expensive one this setting
        # exists to avoid, on ~70k-token requests.
        logger.warning(
            "agent.system_model.resolution_failed.observed",
            model_name=request.model_name,
            purpose=purpose.value,
            exc_info=True,
        )
        return None


async def _vision_runtime(
    request: RuntimeRequest, *, profiles: Callable[[], ProfileResolver]
) -> ResolvedAgentRuntime:
    """A runtime whose model reads images, or ``VisionUnavailableError``.

    Never a fallback to the profile default: that would resolve to the same
    text-only model that created the problem.
    """
    model_name = request.model_name
    if not runtime_system_profiles.system_profile_configured():
        # No system model to point VISION_MODEL at: use a workspace model that
        # declares it reads images, and only such a model.
        workspace = await resolve_workspace_runtime(
            organization_id=request.organization_id,
            user_id=request.user_id,
            model_name=model_name,
            require_vision=True,
        )
        if workspace is None:
            raise VisionUnavailableError(
                "No AI model that reads images is set up. Mark a model as "
                "reading images on one of the workspace's providers in "
                "Settings \u2192 Models."
            )
        return workspace
    # The settings that fix each refusal below go to the log, for whoever runs
    # the deployment; the person whose agent asked is told what is missing.
    if not model_name:
        logger.warning(
            "agent.vision.model_not_configured.refused",
            operator_hint="Set VISION_MODEL to a system model that reads images.",
        )
        raise VisionUnavailableError(_NO_IMAGE_MODEL)
    try:
        resolved = await profiles().resolve(
            runtime=AgentRuntimeConfig(
                profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
                model_name=model_name,
            ),
            organization_id=request.organization_id,
            user_id=request.user_id,
        )
    except RuntimeUnavailableError:
        # The system profile does not offer this name at all; checking
        # capabilities next would blame the wrong thing.
        logger.warning(
            "agent.vision.model_not_offered.refused",
            vision_model=model_name,
            operator_hint=(
                "VISION_MODEL names a model the system provider does not list; "
                "add it to the provider's model names."
            ),
        )
        raise VisionUnavailableError(
            f"The model set up to read images, {model_name}, is not offered by "
            "this deployment."
        ) from None
    entry = resolved.model
    if entry is not None and RuntimeModelCapability.VISION not in entry.capabilities:
        logger.warning(
            "agent.vision.model_not_vision.refused",
            vision_model=model_name,
            operator_hint=(
                "Add VISION_MODEL to LEMMA_OPENAI_VISION_MODEL_NAMES, or point "
                "it at a model that reads images."
            ),
        )
        raise VisionUnavailableError(
            f"The model set up to read images, {model_name}, is not listed as "
            "reading images."
        )
    return resolved
