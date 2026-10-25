"""Agent runtime profile listing and resolution."""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID


from pydantic import HttpUrl
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.transient_errors import raise_if_database_unavailable
from app.core.crypto import get_secret_cipher
from app.core.domain.errors import DomainError
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent.domain.errors import RuntimeUnavailableError
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    AgentHostHarnessHealth,
    AgentHostStatus,
    effective_agent_host_status,
)
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCatalogEntry,
    DEFAULT_HOST_WAIT_TIMEOUT_SECONDS,
    RuntimeProfileScope,
    AgentRuntimeProfile,
    RuntimeProfileKind,
    RuntimeProfileAvailability,
    RuntimeProfileStatus,
    reveal_credentials,
)
from app.modules.agent.domain.value_objects import (
    JsonObject,
    AgentRuntimeConfig,
)
from app.modules.agent.infrastructure.agent_host.repository import AgentHostRepository
from app.modules.agent.infrastructure.runtime_models import (
    AgentHostHarnessModel,
    AgentHostModel,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)

# Imported as a module, not by name: tests patch the discovery functions, and a
# `from ... import f` binding here would keep calling the unpatched original.
from app.modules.agent.services.runtime_profile_creation import (
    RuntimeProfileCreation,
)
from app.modules.agent.services.runtime_system_profiles import (
    DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID as DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
    SERVER_NO_MODEL_OPERATOR_HINT,
    SYSTEM_LEMMA_PROFILE_ID as SYSTEM_LEMMA_PROFILE_ID,
    model_not_configured_error,
    system_lemma_profile,
    system_profile_by_id,
)
from app.modules.agent.domain.resolved_runtime import (
    ResolvedAgentRuntime,
    RuntimeHolder,
    model_unavailable_error,
)
from app.modules.agent.services.runtime_capabilities import (
    unselected_capabilities,
    with_harness_vision,
)

logger = get_logger(__name__)


# Optional hook: an extension (e.g. a cloud provider module) may register a
# customizer that rewrites the system OpenAI-compatible model catalog before it
# is published — typically to map short public names to provider model IDs and
# declare per-model capabilities (vision). The core stays env-driven: without a
# customizer the catalog is used verbatim (public name == provider model name).


class AgentRuntimeProfileService:
    """List and resolve runtime profiles available to a user/org."""

    def __init__(
        self,
        repository: AgentRuntimeProfileRepository | None = None,
        host_repository: AgentHostRepository | None = None,
    ) -> None:
        self.repository = repository
        self.host_repository = host_repository
        self.creation = RuntimeProfileCreation(repository, host_repository)

    def _session(self) -> AsyncSession | None:
        """The session behind the repository, so the connection can be released.

        ``None`` when there is no repository (unit tests); `connection_released`
        treats that as nothing to release and passes straight through.
        """
        return self.repository.uow.session if self.repository is not None else None

    def system_profiles(self) -> list[AgentRuntimeProfile]:
        """The deployment's own profile, or nothing when it is misconfigured.

        A listing degrades; it does not raise. This feeds
        ``agent.runtime.profiles.list`` -- the Models page an operator opens to
        work out why nothing runs -- and a credentials-without-models
        deployment used to 500 it, hiding the very answer the error text
        carries. `resolve()` still raises that error, so the run a person sends
        tells them which setting to fill in.
        """
        try:
            profile = system_lemma_profile()
        except DomainError:
            logger.error(
                "agent.runtime_profile_service.system_profile_unconfigured.degraded",
                exc_info=True,
            )
            return []
        return [profile] if profile is not None else []

    async def list_profiles(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        include_disabled: bool = False,
    ) -> list[AgentRuntimeProfile]:
        profiles = list(self.system_profiles())
        if self.repository is not None:
            profiles.extend(
                await self.repository.get_visible(
                    organization_id=organization_id,
                    user_id=user_id,
                    include_disabled=include_disabled,
                )
            )
        return profiles

    async def list_profiles_with_availability(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        include_disabled: bool = False,
    ) -> list[tuple[AgentRuntimeProfile, RuntimeProfileAvailability | None]]:
        """Every visible profile, paired with whether it can take work now.

        Availability is derived per read, never stored: the same harness profile
        is READY or OFFLINE depending only on whether someone's laptop is awake.
        Two batched queries rather than one per profile.
        """
        profiles = await self.list_profiles(
            organization_id=organization_id,
            user_id=user_id,
            include_disabled=include_disabled,
        )
        harness_ids = {
            profile.harness_id for profile in profiles if profile.harness_id is not None
        }
        if self.host_repository is None or not harness_ids:
            # Two other call sites build this service without a host repository.
            # They never render availability, so degrade rather than fail.
            return [(profile, None) for profile in profiles]

        harnesses = await self.host_repository.get_harnesses(harness_ids)
        hosts = await self.host_repository.get_many(
            {harness.host_id for harness in harnesses.values()}
        )
        return [
            (profile, _profile_availability(profile, harnesses, hosts))
            for profile in profiles
        ]

    async def create_agent_host_profile(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        harness_id: UUID,
        name: str,
        scope: RuntimeProfileScope = RuntimeProfileScope.PERSONAL,
        description: str | None = None,
        default_model_name: str | None = None,
        config_selections: JsonObject | None = None,
        host_wait_timeout_seconds: int = DEFAULT_HOST_WAIT_TIMEOUT_SECONDS,
    ) -> AgentRuntimeProfile:
        """Bind a runtime profile to one harness on one paired Agent Host."""
        return await self.creation.create_agent_host_profile(
            organization_id=organization_id,
            user_id=user_id,
            harness_id=harness_id,
            name=name,
            scope=scope,
            description=description,
            default_model_name=default_model_name,
            config_selections=config_selections,
            host_wait_timeout_seconds=host_wait_timeout_seconds,
        )

    async def create_openai_compatible_profile(
        self,
        *,
        organization_id: UUID,
        name: str,
        base_url: str | HttpUrl,
        api_key: str | None = None,
        description: str | None = None,
        default_model_name: str | None = None,
        model_names: list[str] | None = None,
        vision_model_names: list[str] | None = None,
        reasoning_model_names: list[str] | None = None,
        headers: dict[str, str] | None = None,
        model_settings: dict[str, object] | None = None,
    ) -> AgentRuntimeProfile:
        """See `RuntimeProfileCreation.create_openai_compatible_profile`."""
        return await self.creation.create_openai_compatible_profile(
            organization_id=organization_id,
            name=name,
            base_url=base_url,
            api_key=api_key,
            description=description,
            default_model_name=default_model_name,
            model_names=model_names,
            vision_model_names=vision_model_names,
            reasoning_model_names=reasoning_model_names,
            headers=headers,
            model_settings=model_settings,
        )

    async def create_anthropic_compatible_profile(
        self,
        *,
        organization_id: UUID,
        name: str,
        api_key: str,
        base_url: str | HttpUrl | None = None,
        description: str | None = None,
        default_model_name: str | None = None,
        model_names: list[str] | None = None,
        reasoning_model_names: list[str] | None = None,
        headers: dict[str, str] | None = None,
        model_settings: dict[str, object] | None = None,
    ) -> AgentRuntimeProfile:
        """See `RuntimeProfileCreation.create_anthropic_compatible_profile`."""
        return await self.creation.create_anthropic_compatible_profile(
            organization_id=organization_id,
            name=name,
            api_key=api_key,
            base_url=base_url,
            description=description,
            default_model_name=default_model_name,
            model_names=model_names,
            reasoning_model_names=reasoning_model_names,
            headers=headers,
            model_settings=model_settings,
        )

    async def require_ready_harness(
        self,
        *,
        harness_id: UUID,
        organization_id: UUID,
        user_id: UUID,
        scope: RuntimeProfileScope,
        require_owner: bool = True,
    ) -> AgentHostHarnessModel:
        """See `RuntimeProfileCreation.require_ready_harness`."""
        return await self.creation.require_ready_harness(
            harness_id=harness_id,
            organization_id=organization_id,
            user_id=user_id,
            scope=scope,
            require_owner=require_owner,
        )

    async def resolve(
        self,
        *,
        runtime: AgentRuntimeConfig | None,
        organization_id: UUID | None,
        user_id: UUID,
    ) -> ResolvedAgentRuntime:
        if runtime is None:
            runtime = self.system_default_runtime_config()
        profile_id = runtime.profile_id
        profile = await self.get_profile(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
        )
        if profile is None:
            if profile_id == SYSTEM_LEMMA_PROFILE_ID:
                # The environment variables that would fix this, for the
                # operator; the person whose run failed is told where models
                # are added instead.
                logger.info(
                    "agent.runtime_profile.model_not_configured.observed",
                    operator_hint=SERVER_NO_MODEL_OPERATOR_HINT,
                )
                raise model_not_configured_error()
            archived = await self._archived_profile(
                profile_id=profile_id,
                organization_id=organization_id,
                user_id=user_id,
            )
            if archived is not None:
                # Archiving is a routine action now, so the agents, conversations
                # and pod defaults still pinned to this profile must say what
                # happened instead of surfacing an opaque 500.
                raise DomainError(
                    f"{archived.name} was retired. Pick another model for this "
                    "teammate, or bring it back in Settings \u2192 Models.",
                    code="runtime_profile_archived",
                    status_code=409,
                )
            owned_elsewhere = await self._someone_elses_personal_profile(
                profile_id=profile_id,
                organization_id=organization_id,
                user_id=user_id,
            )
            if owned_elsewhere is not None:
                raise owned_elsewhere
            raise runtime_profile_unavailable_error()
        model = selected_model(profile, runtime.model_name)
        if model is None and profile.kind is not RuntimeProfileKind.HARNESS:
            raise RuntimeUnavailableError(
                f"{profile.name} has no model to run. Add one in Settings "
                "\u2192 Models, or pick another model for this teammate."
            )
        harness_sees = await self._harness_reads_images(profile)
        model = with_harness_vision(model, harness_sees=harness_sees)
        credentials = reveal_credentials(profile.credentials)
        return ResolvedAgentRuntime(
            profile=profile,
            harness_kind=profile.derived_harness_kind(),
            model=model,
            # Left alone on purpose when nothing is selected. Naming a model
            # here would tell the harness which one to run, and "the harness
            # picks" is the designed meaning of an unpinned profile.
            provider_model_name=model.provider_model_name if model else None,
            credentials=credentials,
            unselected_capabilities=(
                []
                if model is not None
                else unselected_capabilities(profile, harness_sees=harness_sees)
            ),
            selection_overrides=dict(runtime.config_selections or {}),
        )

    async def _harness_reads_images(self, profile: AgentRuntimeProfile) -> bool:
        """Ask the harness itself whether it reads images, not the copy of it.

        A harness profile's catalog is built once, at create time, from whatever
        ``capabilities["images"]`` said then -- and a harness registers before
        its ACP probe lands, so that is very often ``false``. The probe updates
        the harness moments later, but the catalog it was copied into is only
        rebuilt when somebody edits the profile in Models settings. Until then a
        Claude Code or Codex host that reads images natively is described as
        text-only, and `pod_view_document_pages` refuses.
        """
        if (
            profile.kind is not RuntimeProfileKind.HARNESS
            or profile.harness_id is None
            or self.host_repository is None
        ):
            return False
        try:
            harnesses = await self.host_repository.get_harnesses({profile.harness_id})
        except SQLAlchemyError as exc:
            raise_if_database_unavailable(exc)
            # A capability hint is not worth losing a run over, but only a
            # database failure is expected here; anything else is a bug and
            # should surface as one.
            logger.warning(
                "agent.runtime_profile.harness_vision_lookup_failed.degraded",
                exc_info=True,
            )
            return False
        harness = harnesses.get(profile.harness_id)
        if harness is None:
            return False
        return (getattr(harness, "capabilities", None) or {}).get("images") is True

    async def _someone_elses_personal_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID | None,
        user_id: UUID,
    ) -> RuntimeUnavailableError | None:
        """Name the owner when this is another member's own runtime.

        A member may pin a teammate the pod sees to their own Claude Code or
        their own provider; it runs for them. When anybody else's turn reaches
        it, the run must neither borrow the owner's runtime nor fail with no
        reason given, so this says whose it is. Failure path only.
        """
        if self.repository is None or organization_id is None:
            return None
        try:
            profile = await self.repository.get_personal_by_id(
                profile_id=profile_id, organization_id=organization_id
            )
            if profile is None or profile.user_id in (None, user_id):
                return None
            owner = await self.repository.owner_first_name(profile.user_id)
        except SQLAlchemyError as exc:
            raise_if_database_unavailable(exc)
            # A better message is not worth masking why the run cannot start.
            logger.warning(
                "agent.runtime_profile.owner_lookup_failed.degraded",
                profile_id=str(profile_id),
                exc_info=True,
            )
            return None
        return personal_runtime_not_shareable_error(
            profile_name=profile.name, owner_name=owner
        )

    async def _archived_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID | None,
        user_id: UUID,
    ) -> AgentRuntimeProfile | None:
        """One extra lookup, on the failure path only, to tell "archived" from
        "never existed"."""
        if self.repository is None or organization_id is None:
            return None
        try:
            profile = await self.repository.get_visible_by_id(
                profile_id=profile_id,
                organization_id=organization_id,
                user_id=user_id,
                include_disabled=True,
            )
        except Exception:  # noqa: BLE001 - a diagnostic must never mask the real error
            logger.warning(
                "agent.runtime_profile.archived_lookup_failed.degraded",
                profile_id=str(profile_id),
                exc_info=True,
            )
            return None
        if profile is None or profile.status is RuntimeProfileStatus.ACTIVE:
            return None
        return profile

    def system_default_runtime_config(self) -> AgentRuntimeConfig:
        return AgentRuntimeConfig(profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID)

    async def get_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID | None,
        user_id: UUID,
    ) -> AgentRuntimeProfile | None:
        system_profile = system_profile_by_id(profile_id)
        if system_profile is not None:
            return system_profile
        if self.repository is None or organization_id is None:
            return None
        return await self.repository.get_visible_by_id(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
        )


def selected_model(
    profile: AgentRuntimeProfile,
    requested_model_name: str | None,
    *,
    saving_for: RuntimeHolder | None = None,
) -> RuntimeModelCatalogEntry | None:
    """The catalog entry a run uses, or ``None`` when nothing is pinned.

    Never a substitute. A model the profile no longer offers used to be
    swapped for its default, or its first entry, with only a log line: a
    teammate pinned to one model ran -- and was billed -- on another, with
    different behaviour, indefinitely and invisibly. Now the run is refused
    with the reason, and the person picks the model.

    The one ``None`` that is not a refusal: a coding agent whose catalog is
    empty publishes no list to check against, and an unpinned profile there
    means "the agent's own default".

    ``saving_for`` names what the runtime is being saved on, when it is. A
    save may name a model the profile never offered at all, so only a turn --
    on a pin a save already accepted -- can say the model went away.
    """
    model_name = requested_model_name or profile.default_model_name
    if not model_name:
        return None
    for model in profile.model_catalog:
        if model_name == model.name:
            return model
    if profile.harness_id is not None and not profile.model_catalog:
        return None
    logger.warning(
        "agent.runtime_profile.model_unavailable.refused",
        profile_id=profile.id,
        requested_model_name=model_name,
    )
    raise model_unavailable_error(profile.name, model_name, saving_for=saving_for)


def runtime_profile_service_for(
    uow: SqlAlchemyUnitOfWork,
) -> AgentRuntimeProfileService:
    """The one resolver every run path and route builds, over ``uow``.

    Always with the host repository: without it a harness profile's vision
    comes from the catalog copied at creation, often before the harness's own
    probe said it reads images, while the MCP bridge -- which had it -- asked
    the harness. Two resolvers gave one run two answers about whether its
    model can see.
    """
    return AgentRuntimeProfileService(
        AgentRuntimeProfileRepository(uow, encryption=get_secret_cipher()),
        AgentHostRepository(uow),
    )


def _profile_availability(
    profile: AgentRuntimeProfile,
    harnesses: Mapping[UUID, AgentHostHarnessModel],
    hosts: Mapping[UUID, AgentHostModel],
) -> RuntimeProfileAvailability | None:
    """Why a harness-backed profile can or cannot take work.

    ``None`` for a model provider: it is reachable whenever its endpoint is, and
    Lemma has nothing local to report about it.
    """
    if profile.harness_id is None:
        return None
    harness = harnesses.get(profile.harness_id)
    if harness is None:
        return RuntimeProfileAvailability.NOT_INSTALLED
    host = hosts.get(harness.host_id)
    if host is None or host.revoked_at is not None:
        return RuntimeProfileAvailability.UNAVAILABLE
    if (
        effective_agent_host_status(host.status, host.last_seen_at)
        is not AgentHostStatus.ONLINE
    ):
        return RuntimeProfileAvailability.OFFLINE
    if harness.health != AgentHostHarnessHealth.READY.value:
        return RuntimeProfileAvailability.UNAVAILABLE
    return RuntimeProfileAvailability.READY


def runtime_profile_unavailable_error() -> RuntimeUnavailableError:
    """Not visible to the person whose run this is, and not known to be whose.

    Gone, or another member's own runtime when the lookup that would name them
    failed (`personal_runtime_not_shareable_error` is the usual answer for
    that). It used to be a bare RuntimeError -- an opaque failure for everyone
    but the owner.
    """
    return RuntimeUnavailableError(
        "The model this teammate is set to use isn't available to you: it was "
        "removed, or it is someone's personal model. Pick another model for "
        "the teammate.",
        code="runtime_profile_unavailable",
    )


def personal_runtime_not_shareable_error(
    *, profile_name: str, owner_name: str | None
) -> RuntimeUnavailableError:
    """Another member's own runtime, reached by someone who is not them."""
    whose = f"{owner_name}'s" if owner_name else "a teammate's"
    ask = f"Ask {owner_name}" if owner_name else "Ask its owner"
    return RuntimeUnavailableError(
        f"This teammate runs on {whose} own {profile_name}, which only they can "
        f"use. {ask} to run it, or pick a model the organization shares.",
        code="runtime_profile_not_shareable",
    )
