"""Agent runtime profile listing and resolution."""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from dotenv import load_dotenv
from pydantic import HttpUrl

from sqlalchemy.exc import SQLAlchemyError

from app.core.config import reveal_secret, settings
from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.agent.domain.agent_host import (
    AgentHostHarnessHealth,
    AgentHostStatus,
    effective_agent_host_status,
)
from app.modules.agent.domain.agent_host_selections import (
    validate_agent_host_model,
    validate_agent_host_selections,
)
from app.modules.agent.domain.runtime_profiles import (
    AnthropicCompatibleRuntimeConfig,
    AgentRuntimeProfile,
    ApiKeyRuntimeCredentials,
    HarnessRuntimeConfig,
    OpenAICompatibleRuntimeConfig,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileAvailability,
    RuntimeProfileScope,
    RuntimeProfileStatus,
    reveal_credentials,
)
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    HarnessKind,
    JsonObject,
)
from app.modules.agent.infrastructure.agent_host_repository import AgentHostRepository
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)

# Imported as a module, not by name: tests patch the discovery functions, and a
# `from ... import f` binding here would keep calling the unpatched original.
from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.agent.services import runtime_provider_discovery as discovery

logger = get_logger(__name__)

SYSTEM_LEMMA_PROFILE_ID = "system:lemma"
DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID = SYSTEM_LEMMA_PROFILE_ID


def _openai_compat_vision_model_names() -> set[str]:
    """Model names the operator declared as image-capable for the system
    OpenAI-compatible profile (``LEMMA_OPENAI_VISION_MODEL_NAMES``).

    The standard OpenAI ``/models`` endpoint does not report modalities, so the
    image-returning tools (``view_image``) can only be enabled safely when the
    operator opts a model in here. A text-only model that receives image content
    breaks the conversation, so the default is empty (no vision).
    """
    raw = os.getenv("LEMMA_OPENAI_VISION_MODEL_NAMES")
    if raw is None:
        raw = settings.lemma_openai_vision_model_names
    return {name.strip() for name in (raw or "").split(",") if name.strip()}


# Optional hook: an extension (e.g. a cloud provider module) may register a
# customizer that rewrites the system OpenAI-compatible model catalog before it
# is published — typically to map short public names to provider model IDs and
# declare per-model capabilities (vision). The core stays env-driven: without a
# customizer the catalog is used verbatim (public name == provider model name).
SystemOpenAICatalogCustomizer = Callable[
    [list[RuntimeModelCatalogEntry]], list[RuntimeModelCatalogEntry]
]

_system_openai_catalog_customizer: SystemOpenAICatalogCustomizer | None = None


def register_system_openai_catalog_customizer(
    customizer: SystemOpenAICatalogCustomizer | None,
) -> None:
    """Register (or clear with ``None``) the system OpenAI catalog customizer.

    Call once at application startup from an extension module. The customizer
    receives the env-built catalog — each entry with ``provider_model_name ==
    name`` and the TEXT+TOOLS baseline (plus any env-declared vision) — and
    returns a rewritten catalog. This is the supported seam for a provider
    overlay to keep short public model names user-facing while sending the real
    provider model ID to the API and declaring per-model vision, without the
    operator hand-configuring provider IDs in the environment.
    """
    global _system_openai_catalog_customizer
    _system_openai_catalog_customizer = customizer


def _build_system_openai_catalog(
    *, require_models: bool = True
) -> list[RuntimeModelCatalogEntry]:
    """Build the configured system OpenAI catalog, then customize it."""
    _load_runtime_env()
    raw_model_names = (
        os.getenv("LEMMA_OPENAI_MODEL_NAMES") or settings.lemma_openai_model_names
    )
    model_names = (
        _csv_setting(raw_model_names)
        if require_models
        else _csv_setting_or_empty(raw_model_names)
    )
    default_model_name = (
        os.getenv("LEMMA_OPENAI_DEFAULT_MODEL") or settings.lemma_openai_default_model
    ).strip()
    if default_model_name and default_model_name not in model_names:
        model_names.insert(0, default_model_name)
    vision_model_names = _openai_compat_vision_model_names()
    catalog = [
        RuntimeModelCatalogEntry(
            name=model_name,
            display_name=_display_model_name(model_name),
            # The operator configures the exact provider model IDs, so the public
            # name is the provider name unless a customizer remaps it.
            provider_model_name=model_name,
            capabilities=_openai_compat_model_capabilities(
                model_name, vision_model_names
            ),
        )
        for model_name in model_names
    ]
    if _system_openai_catalog_customizer is not None:
        catalog = _system_openai_catalog_customizer(catalog)
    return catalog


def system_lemma_openai_catalog_model_names() -> list[tuple[str, str | None]]:
    """Return public/provider model pairs for pricing coverage checks."""
    return [
        (entry.name, entry.provider_model_name)
        for entry in _build_system_openai_catalog(require_models=False)
    ]


def _openai_compat_model_capabilities(
    model_name: str,
    vision_model_names: set[str],
) -> list[RuntimeModelCapability]:
    capabilities = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
    if model_name in vision_model_names:
        capabilities.append(RuntimeModelCapability.VISION)
    return capabilities


@dataclass(slots=True)
class ResolvedAgentRuntime:
    profile: AgentRuntimeProfile
    harness_kind: HarnessKind
    model: RuntimeModelCatalogEntry | None
    provider_model_name: str | None
    credentials: dict[str, object] | None
    # What the runtime can do when no catalog entry is selected. A harness
    # profile routinely pins no model -- the harness picks its own -- and
    # capabilities must survive that, because "nothing selected" says nothing
    # about whether the thing on the other end can read an image.
    unselected_capabilities: list[RuntimeModelCapability] = field(default_factory=list)

    @property
    def model_name_for_harness(self) -> str:
        if self.model is None:
            return "default"
        return self.provider_model_name or self.model.name

    def public_snapshot(self) -> dict[str, object | None]:
        return {
            "profile_id": self.profile.id,
            "profile_name": self.profile.name,
            "user_id": str(self.profile.user_id) if self.profile.user_id else None,
            "harness_id": str(self.profile.harness_id)
            if self.profile.harness_id
            else None,
            "scope": self.profile.scope.value,
            "protocol": self.profile.protocol.value,
            "model_name": self.model.name if self.model else None,
            "provider_model_name": self.provider_model_name,
            # Carried so paths that rebuild a context from the snapshot — the
            # MCP bridges, notably — can work out whether this model reads
            # images, instead of assuming it cannot and delegating needlessly.
            # Sourced from the selected model when there is one, and from the
            # runtime itself when there is not. It used to be `[] if not
            # self.model`, which read "this model cannot see" for every Agent
            # Host run -- those pin no model -- so `pod_view_document_pages`
            # refused and `view_image` was withheld from hosts that read images
            # natively.
            "model_capabilities": [
                capability.value
                for capability in (
                    self.model.capabilities
                    if self.model
                    else self.unselected_capabilities
                )
            ],
            "config": _config_dict(self.profile.config),
        }


class AgentRuntimeProfileService:
    """List and resolve runtime profiles available to a user/org."""

    def __init__(
        self,
        repository: AgentRuntimeProfileRepository | None = None,
        host_repository: AgentHostRepository | None = None,
    ):
        self.repository = repository
        self.host_repository = host_repository

    def _session(self):
        """The session behind the repository, so the connection can be released.

        ``None`` when there is no repository (unit tests); `connection_released`
        treats that as nothing to release and passes straight through.
        """
        return getattr(getattr(self.repository, "uow", None), "session", None)

    def system_profiles(self) -> list[AgentRuntimeProfile]:
        profile = _system_lemma_profile()
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
        # See CreateAgentHostRuntimeProfileRequest: sharing one person's machine
        # with a whole workspace is never the thing a caller meant by saying
        # nothing.
        scope: RuntimeProfileScope = RuntimeProfileScope.PERSONAL,
        description: str | None = None,
        default_model_name: str | None = None,
        config_selections: JsonObject | None = None,
        host_wait_timeout_seconds: int = 300,
    ) -> AgentRuntimeProfile:
        """Bind a runtime profile to one harness on one paired Agent Host.

        The harness must be live and READY at save time: a profile written
        against an offline or unhealthy harness would only fail later, at
        dispatch, with far less context about what went wrong.
        """
        if self.repository is None:
            raise RuntimeError("Runtime profile repository is required")
        if self.host_repository is None:
            raise RuntimeError("Agent Host repository is required")
        if scope not in {
            RuntimeProfileScope.ORGANIZATION,
            RuntimeProfileScope.PERSONAL,
        }:
            raise ValueError(
                "Agent Host profile scope must be ORGANIZATION or PERSONAL"
            )

        normalized_name = _normalize_profile_name(name)
        harness = await self.require_ready_harness(
            harness_id=harness_id,
            organization_id=organization_id,
            user_id=user_id,
            scope=scope,
        )
        config_options = list(harness.config_options or [])
        selections = validate_agent_host_selections(
            config_options=config_options,
            selections=config_selections or {},
        )
        selected_model = validate_agent_host_model(
            config_options=config_options,
            model_name=default_model_name,
        )
        profile = AgentRuntimeProfile(
            id=str(uuid4()),
            organization_id=organization_id,
            user_id=user_id if scope is RuntimeProfileScope.PERSONAL else None,
            harness_id=harness_id,
            scope=scope,
            kind=RuntimeProfileKind.HARNESS,
            protocol=RuntimeProfileProtocol.AGENT_HOST,
            name=normalized_name,
            description=description.strip() if description else None,
            default_model_name=selected_model,
            model_catalog=_agent_host_model_catalog(
                config_options,
                # ``images`` is the one harness capability the server branches
                # on: it is what puts view_image in the run's toolset.
                supports_images=bool(
                    (harness.capabilities or {}).get("images") is True
                ),
            ),
            config=HarnessRuntimeConfig(
                harness_snapshot_revision=harness.config_revision,
                config_selections=selections,
                host_wait_timeout_seconds=host_wait_timeout_seconds,
            ),
            status=RuntimeProfileStatus.ACTIVE,
            metadata={
                "source": "AGENT_HOST",
                "harness_key": harness.harness_key,
            },
        )
        return await self.repository.create(profile)

    async def require_ready_harness(
        self,
        *,
        harness_id: UUID,
        organization_id: UUID,
        user_id: UUID,
        scope: RuntimeProfileScope,
        require_owner: bool = True,
    ):
        assert self.host_repository is not None
        harness = await self.host_repository.get_harness(harness_id=harness_id)
        if harness is None:
            raise ValueError("Agent Host harness is not available")
        if require_owner:
            host = await self.host_repository.get_for_user(
                host_id=harness.host_id,
                user_id=user_id,
            )
        else:
            # Editing an already-bound shared profile is not binding a machine.
            # Requiring ownership here would stop an org admin from fixing a
            # profile a colleague created, while dispatch imposes no such check.
            host = await self.host_repository.get(host_id=harness.host_id)
        if host is None or host.revoked_at is not None:
            raise ValueError("Agent Host harness is not owned by the current user")
        # A paired computer belongs to the person who paired it, not to a
        # workspace: it runs on their machine, with their credentials. Sharing
        # it is the *profile's* decision - giving a profile ORGANIZATION scope
        # is the owner saying "my colleagues may send work here". The host
        # carries no organization at all, and dispatch already works this way:
        # it resolves a run through `harness.host_id` alone.
        if (
            effective_agent_host_status(host.status, host.last_seen_at)
            is not AgentHostStatus.ONLINE
        ):
            raise ValueError("Agent Host is offline or not accepting new runs")
        if harness.health != AgentHostHarnessHealth.READY.value:
            raise ValueError(f"Agent Host harness is not ready: {harness.health}")
        return harness

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
        headers: dict[str, str] | None = None,
        model_settings: dict[str, object] | None = None,
    ) -> AgentRuntimeProfile:
        if self.repository is None:
            raise RuntimeError("Runtime profile repository is required")
        normalized_name = _normalize_profile_name(name)
        normalized_headers = _normalized_headers(headers)
        # Nothing has been read or written yet, so the caller's connection goes
        # back for the provider round trip -- an HTTP call to a base URL the
        # caller supplied, which is as slow as whatever answers it. The writes
        # below re-acquire.
        async with connection_released(self._session()):
            discovered_models = await discovery._discover_openai_compatible_models(
                base_url=str(base_url),
                api_key=api_key,
                headers=normalized_headers,
            )
        catalog = discovery._provider_model_catalog(
            discovered_models=discovered_models,
            fallback_model_names=model_names or [],
            explicit_vision_model_names={
                name.strip() for name in (vision_model_names or []) if name.strip()
            },
        )
        selected_default_model = discovery._select_provider_default_model(
            requested_model_name=default_model_name,
            catalog=catalog,
        )
        profile = AgentRuntimeProfile(
            id=str(uuid4()),
            organization_id=organization_id,
            scope=RuntimeProfileScope.ORGANIZATION,
            kind=RuntimeProfileKind.MODEL_PROVIDER,
            protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
            name=normalized_name,
            description=description.strip() if description else None,
            default_model_name=selected_default_model,
            model_catalog=catalog,
            config=OpenAICompatibleRuntimeConfig(
                base_url=base_url,
                headers=normalized_headers,
                model_settings=model_settings or {},
            ),
            credentials=(
                ApiKeyRuntimeCredentials(api_key=api_key.strip())
                if api_key and api_key.strip()
                else None
            ),
            status=RuntimeProfileStatus.ACTIVE,
            metadata={
                "source": "openai_compatible",
                "catalog_discovered": bool(discovered_models),
            },
        )
        return await _create_profile(self.repository, profile, name=normalized_name)

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
        headers: dict[str, str] | None = None,
        model_settings: dict[str, object] | None = None,
    ) -> AgentRuntimeProfile:
        if self.repository is None:
            raise RuntimeError("Runtime profile repository is required")
        normalized_name = _normalize_profile_name(name)
        normalized_headers = _normalized_headers(headers)
        # Nothing has been read or written yet, so the caller's connection goes
        # back for the provider round trip -- an HTTP call to a base URL the
        # caller supplied, which is as slow as whatever answers it. The writes
        # below re-acquire.
        async with connection_released(self._session()):
            discovered_models = await discovery._discover_anthropic_compatible_models(
                base_url=str(base_url or "https://api.anthropic.com"),
                api_key=api_key,
                headers=normalized_headers,
            )
        catalog = discovery._provider_model_catalog(
            discovered_models=discovered_models,
            fallback_model_names=model_names or [],
            # Anthropic/Claude models are uniformly multimodal, so every model in
            # an Anthropic-compatible profile keeps the vision tools.
            default_vision=True,
        )
        selected_default_model = discovery._select_provider_default_model(
            requested_model_name=default_model_name,
            catalog=catalog,
        )
        profile = AgentRuntimeProfile(
            id=str(uuid4()),
            organization_id=organization_id,
            scope=RuntimeProfileScope.ORGANIZATION,
            kind=RuntimeProfileKind.MODEL_PROVIDER,
            protocol=RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
            name=normalized_name,
            description=description.strip() if description else None,
            default_model_name=selected_default_model,
            model_catalog=catalog,
            config=AnthropicCompatibleRuntimeConfig(
                base_url=base_url,
                headers=normalized_headers,
                model_settings=model_settings or {},
            ),
            credentials=ApiKeyRuntimeCredentials(api_key=api_key.strip()),
            status=RuntimeProfileStatus.ACTIVE,
            metadata={
                "source": "anthropic_compatible",
                "catalog_discovered": bool(discovered_models),
            },
        )
        return await _create_profile(self.repository, profile, name=normalized_name)

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
                raise DomainError(
                    "No LLM model is configured on this server. "
                    "Set LEMMA_OPENAI_API_KEY (plus LEMMA_OPENAI_BASE_URL if not OpenAI) "
                    "or LEMMA_ANTHROPIC_API_KEY with LEMMA_DEFAULT_MODEL_TYPE=anthropic_compat.",
                    code="model_not_configured",
                    status_code=503,
                )
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
                    f"The model {archived.name!r} was removed from this workspace. "
                    "Pick another one, or restore it in Models settings.",
                    code="runtime_profile_archived",
                    status_code=409,
                )
            raise RuntimeError(f"Agent runtime profile {profile_id!r} is not available")
        model = _selected_model(profile, runtime.model_name)
        if model is None and profile.kind is not RuntimeProfileKind.HARNESS:
            raise RuntimeError(
                f"Agent runtime profile {profile_id!r} has no selectable model"
            )
        harness_sees = await self._harness_reads_images(profile)
        model = _with_harness_vision(model, harness_sees=harness_sees)
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
                else _unselected_capabilities(profile, harness_sees=harness_sees)
            ),
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
        except SQLAlchemyError:
            # A capability hint is not worth losing a run over, but only a
            # database failure is expected here; anything else is a bug and
            # should surface as one.
            logger.debug(
                "agent.runtime_profile.harness_vision_lookup_failed.diagnostic",
                exc_info=True,
            )
            return False
        harness = harnesses.get(profile.harness_id)
        if harness is None:
            return False
        return (getattr(harness, "capabilities", None) or {}).get("images") is True

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
        system_profile = _system_profile_by_id(profile_id)
        if system_profile is not None:
            return system_profile
        if self.repository is None or organization_id is None:
            return None
        return await self.repository.get_visible_by_id(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
        )


def _system_lemma_profile() -> AgentRuntimeProfile | None:
    _load_runtime_env()
    model_type = (
        os.getenv("LEMMA_DEFAULT_MODEL_TYPE") or settings.lemma_default_model_type
    ).strip()
    if model_type == "anthropic_compat":
        return _system_lemma_anthropic_profile()
    return _system_lemma_openai_profile()


def _system_lemma_openai_profile() -> AgentRuntimeProfile | None:
    api_key = _env_or_setting("LEMMA_OPENAI_API_KEY", settings.lemma_openai_api_key)
    if not api_key:
        return None
    # Configured credentials require at least one explicit model.
    model_catalog = _build_system_openai_catalog()
    default_model_name = (
        os.getenv("LEMMA_OPENAI_DEFAULT_MODEL") or settings.lemma_openai_default_model
    ).strip()
    return AgentRuntimeProfile(
        id=SYSTEM_LEMMA_PROFILE_ID,
        scope=RuntimeProfileScope.SYSTEM,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=RuntimeProfileProtocol.OPENAI_COMPATIBLE,
        name="Lemma",
        description="System Lemma model provider",
        default_model_name=default_model_name or model_catalog[0].name,
        model_catalog=model_catalog,
        config=OpenAICompatibleRuntimeConfig(
            base_url=os.getenv("LEMMA_OPENAI_BASE_URL")
            or settings.lemma_openai_base_url,
        ),
        credentials=ApiKeyRuntimeCredentials(api_key=api_key),
    )


def _system_lemma_anthropic_profile() -> AgentRuntimeProfile | None:
    api_key = _env_or_setting(
        "LEMMA_ANTHROPIC_API_KEY", settings.lemma_anthropic_api_key
    )
    if not api_key:
        return None
    model_names = _csv_setting(
        os.getenv("LEMMA_ANTHROPIC_MODEL_NAMES") or settings.lemma_anthropic_model_names
    )
    default_model_name = (
        os.getenv("LEMMA_ANTHROPIC_DEFAULT_MODEL")
        or settings.lemma_anthropic_default_model
    ).strip()
    if default_model_name and default_model_name not in model_names:
        model_names.insert(0, default_model_name)
    return AgentRuntimeProfile(
        id=SYSTEM_LEMMA_PROFILE_ID,
        scope=RuntimeProfileScope.SYSTEM,
        kind=RuntimeProfileKind.MODEL_PROVIDER,
        protocol=RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
        name="Lemma",
        description="System Lemma model provider",
        default_model_name=default_model_name or model_names[0],
        model_catalog=[
            RuntimeModelCatalogEntry(
                name=model_name,
                display_name=_display_model_name(model_name),
                provider_model_name=model_name,
                # Claude models are multimodal, so the vision-only `view_image`
                # tool stays available on the Anthropic system profile.
                capabilities=[
                    RuntimeModelCapability.TEXT,
                    RuntimeModelCapability.TOOLS,
                    RuntimeModelCapability.VISION,
                ],
            )
            for model_name in model_names
        ],
        config=AnthropicCompatibleRuntimeConfig(
            base_url=os.getenv("LEMMA_ANTHROPIC_BASE_URL")
            or settings.lemma_anthropic_base_url,
        ),
        credentials=ApiKeyRuntimeCredentials(api_key=api_key),
    )


def _system_profile_by_id(profile_id: str) -> AgentRuntimeProfile | None:
    if profile_id == SYSTEM_LEMMA_PROFILE_ID:
        return _system_lemma_profile()
    return None


def _env_or_setting(env_name: str, setting_value: object | None) -> str | None:
    value = os.getenv(env_name) or reveal_secret(setting_value)
    if value is None:
        return None
    normalized = value.strip()
    return normalized or None


def _csv_setting(value: str) -> list[str]:
    model_names = _csv_setting_or_empty(value)
    if not model_names:
        raise RuntimeError("Lemma system model profile requires at least one model")
    return model_names


def _csv_setting_or_empty(value: str) -> list[str]:
    model_names: list[str] = []
    for raw_model_name in value.split(","):
        model_name = raw_model_name.strip()
        if model_name and model_name not in model_names:
            model_names.append(model_name)
    return model_names


def _display_model_name(model_name: str) -> str:
    return model_name.replace("-", " ").replace("_", " ").title()


def _agent_host_model_catalog(
    config_options: list[Any],
    *,
    supports_images: bool = False,
) -> list[RuntimeModelCatalogEntry]:
    """Advertise the models the harness itself offers, verbatim.

    Agent Host rejects a model the harness does not list, so these names are
    passed straight through as the provider model name — Lemma never renames or
    invents one. A harness with no ``model`` option yields an empty catalog and
    the profile pins no model, which lets the harness use its own default.
    """
    capabilities = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
    if supports_images:
        capabilities.append(RuntimeModelCapability.VISION)
    entries: list[RuntimeModelCatalogEntry] = []
    seen: set[str] = set()
    for raw_option in config_options:
        if not isinstance(raw_option, dict):
            continue
        if str(raw_option.get("category") or "").strip() != "model":
            continue
        for item in raw_option.get("options") or []:
            if isinstance(item, dict):
                name = str(item.get("value") or item.get("id") or "").strip()
                display_name = str(item.get("name") or "").strip() or name
            else:
                name = str(item).strip()
                display_name = name
            if not name or name in seen:
                continue
            seen.add(name)
            entries.append(
                RuntimeModelCatalogEntry(
                    name=name,
                    display_name=display_name,
                    provider_model_name=name,
                    capabilities=list(capabilities),
                )
            )
    return entries


def _with_harness_vision(
    model: RuntimeModelCatalogEntry | None,
    *,
    harness_sees: bool,
) -> RuntimeModelCatalogEntry | None:
    """Additive only: a harness that reports images gains VISION.

    One that does not is left exactly as stored, because the stored catalog is
    what an operator may have deliberately edited.
    """
    if (
        model is None
        or not harness_sees
        or RuntimeModelCapability.VISION in model.capabilities
    ):
        return model
    return model.model_copy(
        update={"capabilities": [*model.capabilities, RuntimeModelCapability.VISION]}
    )


def _unselected_capabilities(
    profile: AgentRuntimeProfile,
    *,
    harness_sees: bool,
) -> list[RuntimeModelCapability]:
    """What the runtime can do when no catalog entry is selected.

    An Agent Host profile routinely pins no model: `_agent_host_model_catalog`
    documents an empty catalog as meaning "let the harness use its own default",
    and a populated catalog with no chosen entry means the same. Either way
    `_selected_model` returns None, and reading capabilities off that None was
    reporting every such runtime as unable to see.

    The catalog is still the better source when it has entries, so it is used --
    but by **intersection**, so a mixed catalog cannot claim a capability only
    some of its models have. `harness_sees` is then additive on top, exactly as
    it is for a selected model.
    """
    baseline = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
    if profile.model_catalog:
        shared = set(profile.model_catalog[0].capabilities)
        for entry in profile.model_catalog[1:]:
            shared &= set(entry.capabilities)
        capabilities = [c for c in profile.model_catalog[0].capabilities if c in shared]
    else:
        capabilities = list(baseline)
    if harness_sees and RuntimeModelCapability.VISION not in capabilities:
        capabilities.append(RuntimeModelCapability.VISION)
    return capabilities


def _profile_availability(
    profile: AgentRuntimeProfile,
    harnesses: dict[UUID, object],
    hosts: dict[UUID, object],
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
    host = hosts.get(getattr(harness, "host_id", None))
    if host is None or getattr(host, "revoked_at", None) is not None:
        return RuntimeProfileAvailability.UNAVAILABLE
    if (
        effective_agent_host_status(
            getattr(host, "status", None), getattr(host, "last_seen_at", None)
        )
        is not AgentHostStatus.ONLINE
    ):
        return RuntimeProfileAvailability.OFFLINE
    if getattr(harness, "health", None) != AgentHostHarnessHealth.READY.value:
        return RuntimeProfileAvailability.UNAVAILABLE
    return RuntimeProfileAvailability.READY


def _normalize_profile_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Profile name cannot be empty")
    return normalized


def _normalized_headers(headers: dict[str, str] | None) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in (headers or {}).items():
        header_name = key.strip()
        header_value = value.strip()
        if header_name and header_value:
            normalized[header_name] = header_value
    return normalized


async def _create_profile(
    repository: AgentRuntimeProfileRepository,
    profile: AgentRuntimeProfile,
    *,
    name: str,
) -> AgentRuntimeProfile:
    del name
    return await repository.create(profile)


def _load_runtime_env() -> None:
    root = Path(__file__).resolve().parents[5]
    backend = Path(__file__).resolve().parents[4]
    load_dotenv(backend / ".env", override=False)
    load_dotenv(root / ".env", override=False)


def _selected_model(
    profile: AgentRuntimeProfile,
    requested_model_name: str | None,
) -> RuntimeModelCatalogEntry | None:
    model_name = requested_model_name or profile.default_model_name
    if not model_name:
        return None
    for model in profile.model_catalog:
        if model_name == model.name:
            return model
    # The requested model is not in the catalog (e.g. a pinned default whose
    # model was later deprecated, or a swapped BYO key). Degrade gracefully to
    # the profile's own default — and then the first catalog entry — rather than
    # hard-failing every run that relies on this profile.
    if requested_model_name:
        if profile.default_model_name:
            for model in profile.model_catalog:
                if profile.default_model_name == model.name:
                    return model
    if profile.model_catalog:
        return profile.model_catalog[0]
    return None


def _config_dict(config: object | None) -> dict[str, object]:
    if config is None:
        return {}
    model_dump = getattr(config, "model_dump", None)
    if callable(model_dump):
        return model_dump(mode="json")
    if isinstance(config, dict):
        return config
    return {}
