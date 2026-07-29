"""Agent runtime profile listing and resolution."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import cast
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from dotenv import load_dotenv
from pydantic import HttpUrl, SecretStr

from app.core.config import reveal_secret, settings
from app.core.domain.errors import DomainError
from app.core.log.log import get_logger
from app.modules.agent.domain.runtime_profiles import (
    AnthropicCompatibleRuntimeConfig,
    AgentRuntimeProfile,
    ApiKeyRuntimeCredentials,
    AzureOpenAIRuntimeConfig,
    GoogleVertexRuntimeConfig,
    GoogleVertexRuntimeCredentials,
    HarnessRuntimeConfig,
    MODEL_PROVIDER_TYPES,
    OpenAICompatibleRuntimeConfig,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileScope,
    RuntimeProfileStatus,
    RuntimeProfileType,
    reveal_credentials,
)
from app.modules.agent.domain.agent_host import (
    validate_agent_host_model,
    validate_agent_host_selections,
)
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    JsonObject,
)
from app.modules.agent.infrastructure.agent_host_management_repository import (
    AgentHostRepository,
)
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeProfileRepository,
)
from app.modules.agent.services.agent_host_profile_service import (
    create_harness_profile,
    selected_harness_model,
    validate_fallback_profile,
)
from app.modules.agent.services.resolved_runtime import ResolvedAgentRuntime

logger = get_logger(__name__)

SYSTEM_LEMMA_PROFILE_ID = "system:lemma"
DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID = SYSTEM_LEMMA_PROFILE_ID


@dataclass(frozen=True, slots=True)
class DiscoveredModel:
    """Provider-discovered model with best-effort image capability."""

    name: str
    supports_vision: bool = False


def _openai_compat_vision_model_names() -> set[str]:
    """Operator-declared image-capable system OpenAI model names."""
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


class AgentRuntimeProfileService:
    """List and resolve runtime profiles available to a user/org."""

    def __init__(
        self,
        repository: AgentRuntimeProfileRepository | None = None,
        host_repository: AgentHostRepository | None = None,
    ):
        self.repository = repository
        self.host_repository = host_repository

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

    async def create_harness_profile(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        harness_id: UUID,
        scope: RuntimeProfileScope,
        name: str,
        harness_snapshot_revision: str,
        config_selections: JsonObject,
        default_model_name: str | None = None,
        description: str | None = None,
        host_wait_timeout_seconds: int = 300,
        fallback_profile_id: str | None = None,
    ) -> AgentRuntimeProfile:
        return await create_harness_profile(
            self,
            organization_id=organization_id,
            user_id=user_id,
            harness_id=harness_id,
            scope=scope,
            name=name,
            harness_snapshot_revision=harness_snapshot_revision,
            config_selections=config_selections,
            default_model_name=default_model_name,
            description=description,
            host_wait_timeout_seconds=host_wait_timeout_seconds,
            fallback_profile_id=fallback_profile_id,
        )

    async def create_openai_compatible_profile(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        name: str,
        scope: RuntimeProfileScope = RuntimeProfileScope.PERSONAL,
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
        _validate_user_profile_scope(scope)
        normalized_name = _normalize_profile_name(name)
        normalized_headers = _normalized_headers(headers)
        discovered_models = await _discover_openai_compatible_models(
            base_url=str(base_url),
            api_key=api_key,
            headers=normalized_headers,
        )
        catalog = _provider_model_catalog(
            discovered_models=discovered_models,
            fallback_model_names=model_names or [],
            explicit_vision_model_names={
                name.strip() for name in (vision_model_names or []) if name.strip()
            },
        )
        selected_default_model = _select_provider_default_model(
            requested_model_name=default_model_name,
            catalog=catalog,
        )
        profile = AgentRuntimeProfile(
            id=str(uuid4()),
            organization_id=organization_id,
            owner_user_id=(
                user_id if scope is RuntimeProfileScope.PERSONAL else None
            ),
            scope=scope,
            runtime_type=RuntimeProfileType.OPENAI_COMPATIBLE,
            name=normalized_name,
            description=description.strip() if description else None,
            default_model_name=selected_default_model,
            model_catalog=catalog,
            config=OpenAICompatibleRuntimeConfig(
                base_url=HttpUrl(str(base_url)),
                headers=normalized_headers,
                model_settings=model_settings or {},
            ),
            credentials=(
                ApiKeyRuntimeCredentials(api_key=SecretStr(api_key.strip()))
                if api_key and api_key.strip()
                else None
            ),
            status=RuntimeProfileStatus.ACTIVE,
        )
        return await _create_profile(self.repository, profile, name=normalized_name)

    async def create_anthropic_compatible_profile(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        name: str,
        scope: RuntimeProfileScope = RuntimeProfileScope.PERSONAL,
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
        _validate_user_profile_scope(scope)
        normalized_name = _normalize_profile_name(name)
        normalized_headers = _normalized_headers(headers)
        discovered_models = await _discover_anthropic_compatible_models(
            base_url=str(base_url or "https://api.anthropic.com"),
            api_key=api_key,
            headers=normalized_headers,
        )
        catalog = _provider_model_catalog(
            discovered_models=discovered_models,
            fallback_model_names=model_names or [],
            # Anthropic/Claude models are uniformly multimodal, so every model in
            # an Anthropic-compatible profile keeps the vision tools.
            default_vision=True,
        )
        selected_default_model = _select_provider_default_model(
            requested_model_name=default_model_name,
            catalog=catalog,
        )
        profile = AgentRuntimeProfile(
            id=str(uuid4()),
            organization_id=organization_id,
            owner_user_id=(
                user_id if scope is RuntimeProfileScope.PERSONAL else None
            ),
            scope=scope,
            runtime_type=RuntimeProfileType.ANTHROPIC_COMPATIBLE,
            name=normalized_name,
            description=description.strip() if description else None,
            default_model_name=selected_default_model,
            model_catalog=catalog,
            config=AnthropicCompatibleRuntimeConfig(
                base_url=HttpUrl(str(base_url)) if base_url is not None else None,
                headers=normalized_headers,
                model_settings=model_settings or {},
            ),
            credentials=ApiKeyRuntimeCredentials(api_key=SecretStr(api_key.strip())),
            status=RuntimeProfileStatus.ACTIVE,
        )
        return await _create_profile(self.repository, profile, name=normalized_name)

    async def create_azure_openai_profile(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        name: str,
        scope: RuntimeProfileScope,
        azure_endpoint: str | HttpUrl,
        api_version: str | None,
        api_key: str,
        default_model_name: str,
        model_names: list[str],
        description: str | None = None,
        model_settings: JsonObject | None = None,
    ) -> AgentRuntimeProfile:
        if self.repository is None:
            raise RuntimeError("Runtime profile repository is required")
        _validate_user_profile_scope(scope)
        normalized_name = _normalize_profile_name(name)
        catalog = _provider_model_catalog(
            discovered_models=[],
            fallback_model_names=model_names,
        )
        selected_default_model = _select_provider_default_model(
            requested_model_name=default_model_name,
            catalog=catalog,
        )
        profile = AgentRuntimeProfile(
            id=str(uuid4()),
            organization_id=organization_id,
            owner_user_id=(
                user_id if scope is RuntimeProfileScope.PERSONAL else None
            ),
            scope=scope,
            runtime_type=RuntimeProfileType.AZURE_OPENAI,
            name=normalized_name,
            description=description.strip() if description else None,
            default_model_name=selected_default_model,
            model_catalog=catalog,
            config=AzureOpenAIRuntimeConfig(
                azure_endpoint=HttpUrl(str(azure_endpoint)),
                api_version=api_version.strip() if api_version else None,
                model_settings=model_settings or {},
            ),
            credentials=ApiKeyRuntimeCredentials(
                api_key=SecretStr(api_key.strip())
            ),
            status=RuntimeProfileStatus.ACTIVE,
        )
        return await _create_profile(self.repository, profile, name=normalized_name)

    async def create_google_vertex_profile(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        name: str,
        scope: RuntimeProfileScope,
        project_id: str,
        location: str,
        default_model_name: str,
        model_names: list[str],
        service_account_json: JsonObject | None = None,
        description: str | None = None,
        model_settings: JsonObject | None = None,
    ) -> AgentRuntimeProfile:
        if self.repository is None:
            raise RuntimeError("Runtime profile repository is required")
        _validate_user_profile_scope(scope)
        normalized_name = _normalize_profile_name(name)
        catalog = _provider_model_catalog(
            discovered_models=[],
            fallback_model_names=model_names,
            default_vision=True,
        )
        selected_default_model = _select_provider_default_model(
            requested_model_name=default_model_name,
            catalog=catalog,
        )
        profile = AgentRuntimeProfile(
            id=str(uuid4()),
            organization_id=organization_id,
            owner_user_id=(
                user_id if scope is RuntimeProfileScope.PERSONAL else None
            ),
            scope=scope,
            runtime_type=RuntimeProfileType.GOOGLE_VERTEX,
            name=normalized_name,
            description=description.strip() if description else None,
            default_model_name=selected_default_model,
            model_catalog=catalog,
            config=GoogleVertexRuntimeConfig(
                project_id=project_id.strip(),
                location=location.strip(),
                model_settings=model_settings or {},
            ),
            credentials=(
                GoogleVertexRuntimeCredentials(
                    service_account_json=service_account_json
                )
                if service_account_json is not None
                else None
            ),
            status=RuntimeProfileStatus.ACTIVE,
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
            raise RuntimeError(f"Agent runtime profile {profile_id!r} is not available")
        model = _selected_model(profile, runtime.model_name)
        if model is None and profile.runtime_type is not RuntimeProfileType.HARNESS:
            raise RuntimeError(
                f"Agent runtime profile {profile_id!r} has no selectable model"
            )
        credentials = reveal_credentials(profile.credentials)
        return ResolvedAgentRuntime(
            profile=profile,
            harness_kind=profile.derived_harness_kind(),
            model=model,
            provider_model_name=model.provider_model_name if model else None,
            credentials=credentials,
        )

    async def update_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
        changes: dict[str, object],
    ) -> AgentRuntimeProfile:
        if self.repository is None:
            raise RuntimeError("Runtime profile repository is required")
        profile = await self.get_profile(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            include_disabled=True,
        )
        if profile is None or profile.scope is RuntimeProfileScope.SYSTEM:
            raise ValueError("Runtime profile is not available")

        common: dict[str, object] = {}
        if "name" in changes:
            common["name"] = _normalize_profile_name(str(changes["name"]))
        if "description" in changes:
            description = changes["description"]
            common["description"] = (
                str(description).strip() if description is not None else None
            )
        if "status" in changes:
            common["status"] = changes["status"]
        profile = profile.model_copy(update=common)

        harness_changes = {
            "default_model_name",
            "harness_snapshot_revision",
            "config_selections",
            "host_wait_timeout_seconds",
            "fallback_profile_id",
        }
        provider_changes = {
            "default_model_name",
            "base_url",
            "azure_endpoint",
            "api_version",
            "project_id",
            "location",
            "service_account_json",
            "api_key",
            "model_names",
            "headers",
            "model_settings",
            "refresh_provider_catalog",
        }
        if (
            profile.runtime_type is RuntimeProfileType.HARNESS
            and harness_changes.intersection(changes)
        ):
            profile = await self._update_harness_profile(
                profile,
                user_id=user_id,
                changes=changes,
            )
        elif (
            profile.runtime_type in MODEL_PROVIDER_TYPES
            and provider_changes.intersection(changes)
        ):
            profile = await self._update_provider_profile(profile, changes=changes)
        elif profile.runtime_type not in MODEL_PROVIDER_TYPES | {
            RuntimeProfileType.HARNESS
        }:
            raise ValueError("Unsupported runtime profile type")
        profile = AgentRuntimeProfile.model_validate(profile.model_dump())
        return await self.repository.update(profile)

    async def disable_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
    ) -> AgentRuntimeProfile:
        return await self.update_profile(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            changes={"status": RuntimeProfileStatus.DISABLED},
        )

    async def refresh_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID,
        user_id: UUID,
    ) -> AgentRuntimeProfile:
        profile = await self.get_profile(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            include_disabled=True,
        )
        if profile is None or profile.scope is RuntimeProfileScope.SYSTEM:
            raise ValueError("Runtime profile is not available")
        if profile.runtime_type is RuntimeProfileType.HARNESS:
            if self.host_repository is None or profile.harness_id is None:
                raise RuntimeError("Agent Host repository is required")
            harness = await self.host_repository.get_harness(
                harness_id=profile.harness_id
            )
            if harness is None:
                raise ValueError("Agent Host harness is not available")
            config = cast(HarnessRuntimeConfig, profile.config)
            validate_agent_host_selections(
                config_options=harness.config_options or [],
                selections=config.config_selections,
            )
            validate_agent_host_model(
                config_options=harness.config_options or [],
                model_name=profile.default_model_name,
            )
            return await self.update_profile(
                profile_id=profile_id,
                organization_id=organization_id,
                user_id=user_id,
                changes={"harness_snapshot_revision": harness.config_revision},
            )
        return await self.update_profile(
            profile_id=profile_id,
            organization_id=organization_id,
            user_id=user_id,
            changes={"refresh_provider_catalog": True},
        )

    async def _update_harness_profile(
        self,
        profile: AgentRuntimeProfile,
        *,
        user_id: UUID,
        changes: dict[str, object],
    ) -> AgentRuntimeProfile:
        provider_fields = {
            "base_url",
            "azure_endpoint",
            "api_version",
            "project_id",
            "location",
            "service_account_json",
            "api_key",
            "model_names",
            "headers",
            "model_settings",
        }
        if provider_fields.intersection(changes):
            raise ValueError("Provider settings cannot be applied to a harness profile")
        if self.host_repository is None or profile.harness_id is None:
            raise RuntimeError("Agent Host repository is required")
        harness = await self.host_repository.get_harness(
            harness_id=profile.harness_id
        )
        if harness is None:
            raise ValueError("Agent Host harness is not available")
        config = cast(HarnessRuntimeConfig, profile.config)
        revision = str(
            changes.get(
                "harness_snapshot_revision",
                config.harness_snapshot_revision,
            )
        )
        if revision != harness.config_revision:
            raise ValueError(
                "Agent Host harness changed; refresh configuration before saving"
            )
        raw_selections = changes.get(
            "config_selections",
            config.config_selections,
        )
        if not isinstance(raw_selections, dict):
            raise ValueError("config_selections must be an object")
        selections = validate_agent_host_selections(
            config_options=harness.config_options or [],
            selections=raw_selections,
        )
        default_model = cast(
            str | None,
            changes.get("default_model_name", profile.default_model_name),
        )
        default_model = validate_agent_host_model(
            config_options=harness.config_options or [],
            model_name=default_model,
        )
        fallback = cast(
            str | None,
            changes.get("fallback_profile_id", config.fallback_profile_id),
        )
        if profile.organization_id is None:
            raise ValueError("User runtime profiles require an organization")
        await validate_fallback_profile(
            service=self,
            fallback_profile_id=fallback,
            organization_id=profile.organization_id,
            user_id=user_id,
        )
        return profile.model_copy(
            update={
                "default_model_name": default_model,
                "config": HarnessRuntimeConfig(
                    harness_snapshot_revision=revision,
                    config_selections=selections,
                    host_wait_timeout_seconds=int(
                        changes.get(
                            "host_wait_timeout_seconds",
                            config.host_wait_timeout_seconds,
                        )
                    ),
                    fallback_profile_id=fallback,
                ),
            }
        )

    async def _update_provider_profile(
        self,
        profile: AgentRuntimeProfile,
        *,
        changes: dict[str, object],
    ) -> AgentRuntimeProfile:
        harness_fields = {
            "harness_snapshot_revision",
            "config_selections",
            "host_wait_timeout_seconds",
            "fallback_profile_id",
        }
        if harness_fields.intersection(changes):
            raise ValueError("Harness settings cannot be applied to a provider profile")
        credentials = reveal_credentials(profile.credentials) or {}
        api_key = cast(str | None, credentials.get("api_key"))
        if "api_key" in changes:
            api_key = cast(str | None, changes["api_key"])
        fallback_names = cast(
            list[str],
            changes.get(
                "model_names",
                [entry.name for entry in profile.model_catalog],
            ),
        )

        if profile.runtime_type is RuntimeProfileType.OPENAI_COMPATIBLE:
            current = cast(OpenAICompatibleRuntimeConfig, profile.config)
            base_url = str(changes.get("base_url", current.base_url))
            headers = _normalized_headers(
                cast(dict[str, str], changes.get("headers", current.headers))
            )
            discovered = await _discover_openai_compatible_models(
                base_url=base_url,
                api_key=api_key,
                headers=headers,
            )
            catalog = _provider_model_catalog(
                discovered_models=discovered,
                fallback_model_names=fallback_names,
            )
            updated_config: object = OpenAICompatibleRuntimeConfig(
                base_url=HttpUrl(base_url),
                headers=headers,
                model_settings=cast(
                    dict[str, object],
                    changes.get("model_settings", current.model_settings),
                ),
            )
        elif profile.runtime_type is RuntimeProfileType.ANTHROPIC_COMPATIBLE:
            if not api_key:
                raise ValueError("Anthropic-compatible profiles require api_key")
            current = cast(AnthropicCompatibleRuntimeConfig, profile.config)
            base_url = str(
                changes.get(
                    "base_url",
                    current.base_url or "https://api.anthropic.com",
                )
            )
            headers = _normalized_headers(
                cast(dict[str, str], changes.get("headers", current.headers))
            )
            discovered = await _discover_anthropic_compatible_models(
                base_url=base_url,
                api_key=api_key,
                headers=headers,
            )
            catalog = _provider_model_catalog(
                discovered_models=discovered,
                fallback_model_names=fallback_names,
                default_vision=True,
            )
            updated_config = AnthropicCompatibleRuntimeConfig(
                base_url=HttpUrl(base_url),
                headers=headers,
                model_settings=cast(
                    dict[str, object],
                    changes.get("model_settings", current.model_settings),
                ),
            )
        elif profile.runtime_type is RuntimeProfileType.AZURE_OPENAI:
            if not api_key:
                raise ValueError("Azure OpenAI profiles require api_key")
            current = cast(AzureOpenAIRuntimeConfig, profile.config)
            endpoint = str(
                changes.get("azure_endpoint", current.azure_endpoint)
            )
            catalog = _provider_model_catalog(
                discovered_models=[],
                fallback_model_names=fallback_names,
            )
            updated_config = AzureOpenAIRuntimeConfig(
                azure_endpoint=HttpUrl(endpoint),
                api_version=cast(
                    str | None,
                    changes.get("api_version", current.api_version),
                ),
                model_settings=cast(
                    dict[str, object],
                    changes.get("model_settings", current.model_settings),
                ),
            )
        elif profile.runtime_type is RuntimeProfileType.GOOGLE_VERTEX:
            current = cast(GoogleVertexRuntimeConfig, profile.config)
            catalog = _provider_model_catalog(
                discovered_models=[],
                fallback_model_names=fallback_names,
                default_vision=True,
            )
            updated_config = GoogleVertexRuntimeConfig(
                project_id=str(
                    changes.get("project_id", current.project_id)
                ).strip(),
                location=str(changes.get("location", current.location)).strip(),
                model_settings=cast(
                    dict[str, object],
                    changes.get("model_settings", current.model_settings),
                ),
            )
        else:
            raise ValueError("This provider type cannot be updated yet")

        selected_default = _select_provider_default_model(
            requested_model_name=cast(
                str | None,
                changes.get("default_model_name", profile.default_model_name),
            ),
            catalog=catalog,
        )
        return profile.model_copy(
            update={
                "default_model_name": selected_default,
                "model_catalog": catalog,
                "config": updated_config,
                "credentials": _updated_provider_credentials(
                    profile=profile,
                    changes=changes,
                    api_key=api_key,
                ),
            }
        )

    def system_default_runtime_config(self) -> AgentRuntimeConfig:
        return AgentRuntimeConfig(profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID)

    async def get_profile(
        self,
        *,
        profile_id: str,
        organization_id: UUID | None,
        user_id: UUID,
        include_disabled: bool = False,
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
            include_disabled=include_disabled,
        )


def _updated_provider_credentials(
    *,
    profile: AgentRuntimeProfile,
    changes: dict[str, object],
    api_key: str | None,
) -> object | None:
    if profile.runtime_type is RuntimeProfileType.GOOGLE_VERTEX:
        if "service_account_json" not in changes:
            return profile.credentials
        service_account_json = changes["service_account_json"]
        if service_account_json is None:
            return None
        if not isinstance(service_account_json, dict):
            raise ValueError("service_account_json must be an object")
        return GoogleVertexRuntimeCredentials(
            service_account_json=service_account_json
        )
    return (
        ApiKeyRuntimeCredentials(api_key=SecretStr(api_key))
        if api_key
        else None
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
        runtime_type=RuntimeProfileType.OPENAI_COMPATIBLE,
        name="Lemma",
        description="System Lemma model provider",
        default_model_name=default_model_name or model_catalog[0].name,
        model_catalog=model_catalog,
        config=OpenAICompatibleRuntimeConfig(
            base_url=HttpUrl(
                os.getenv("LEMMA_OPENAI_BASE_URL") or settings.lemma_openai_base_url
            ),
        ),
        credentials=ApiKeyRuntimeCredentials(api_key=SecretStr(api_key)),
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
        runtime_type=RuntimeProfileType.ANTHROPIC_COMPATIBLE,
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
            base_url=HttpUrl(
                os.getenv("LEMMA_ANTHROPIC_BASE_URL")
                or settings.lemma_anthropic_base_url
            ),
        ),
        credentials=ApiKeyRuntimeCredentials(api_key=SecretStr(api_key)),
    )


def _system_profile_by_id(profile_id: str) -> AgentRuntimeProfile | None:
    if profile_id == SYSTEM_LEMMA_PROFILE_ID:
        return _system_lemma_profile()
    return None


def _env_or_setting(env_name: str, setting_value: SecretStr | str | None) -> str | None:
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


def _normalize_profile_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise ValueError("Profile name cannot be empty")
    return normalized


def _validate_user_profile_scope(scope: RuntimeProfileScope) -> None:
    if scope not in {
        RuntimeProfileScope.PERSONAL,
        RuntimeProfileScope.ORGANIZATION,
    }:
        raise ValueError("User-created profiles must be PERSONAL or ORGANIZATION")


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


def _provider_model_catalog(
    *,
    discovered_models: list[DiscoveredModel],
    fallback_model_names: list[str],
    explicit_vision_model_names: set[str] | None = None,
    default_vision: bool = False,
) -> list[RuntimeModelCatalogEntry]:
    """Build a model catalog, marking each model VISION-capable when the
    provider advertised image input (``DiscoveredModel.supports_vision``), the
    caller declared it (``explicit_vision_model_names``), or the protocol is
    universally multimodal (``default_vision`` — e.g. Anthropic/Claude).

    Caller-supplied ``fallback_model_names`` (used when discovery yields nothing)
    carry no modality data, so they get vision only via the explicit override or
    ``default_vision``.
    """
    explicit = explicit_vision_model_names or set()
    vision_by_name: dict[str, bool] = {}
    order: list[str] = []
    for discovered in discovered_models:
        name = discovered.name.strip()
        if name and name not in vision_by_name:
            order.append(name)
            vision_by_name[name] = discovered.supports_vision
    for model_name in fallback_model_names:
        name = model_name.strip()
        if name and name not in vision_by_name:
            order.append(name)
            vision_by_name[name] = False
    if not order:
        raise ValueError(
            "Provider model catalog could not be discovered; provide model_names"
        )
    catalog: list[RuntimeModelCatalogEntry] = []
    for name in order:
        supports_vision = default_vision or vision_by_name[name] or name in explicit
        capabilities = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
        if supports_vision:
            capabilities.append(RuntimeModelCapability.VISION)
        catalog.append(
            RuntimeModelCatalogEntry(
                name=name,
                display_name=name,
                provider_model_name=name,
                capabilities=capabilities,
            )
        )
    return catalog


def _select_provider_default_model(
    *,
    requested_model_name: str | None,
    catalog: list[RuntimeModelCatalogEntry],
) -> str:
    if requested_model_name is None:
        return catalog[0].name
    normalized = requested_model_name.strip()
    catalog_names = {model.name for model in catalog}
    if normalized not in catalog_names:
        raise ValueError("default_model_name must be one of the provider model names")
    return normalized


async def _discover_openai_compatible_models(
    *,
    base_url: str,
    api_key: str | None,
    headers: dict[str, str],
) -> list[DiscoveredModel]:
    request_headers = dict(headers)
    if api_key:
        request_headers.setdefault("Authorization", f"Bearer {api_key}")
    return await _discover_models(
        url=_join_url(base_url, "models"),
        headers=request_headers,
        parser=_parse_openai_compatible_models,
    )


async def _discover_anthropic_compatible_models(
    *,
    base_url: str,
    api_key: str,
    headers: dict[str, str],
) -> list[DiscoveredModel]:
    request_headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        **headers,
    }
    return await _discover_models(
        url=_join_url(base_url, "models"),
        headers=request_headers,
        parser=_parse_openai_compatible_models,
    )


_PUBLIC_URL_ERROR = "base_url must be a public http(s) URL"


async def _validate_public_base_url(url: str) -> None:
    """Reject SSRF targets before issuing a server-side request to ``url``.

    A model provider's ``base_url`` is caller-supplied, so block non-http(s)
    schemes and any host that resolves to a loopback/private/link-local/reserved
    address (e.g. ``http://169.254.169.254/`` cloud metadata, ``http://10.x``).
    Loopback is permitted in local/testing mode so development against a model
    server on localhost still works. (Note: this validates at resolve time; it
    does not pin the connection, so it is not fully DNS-rebinding-proof — it
    closes the practical metadata/internal-service vector.)
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(_PUBLIC_URL_ERROR)
    host = parsed.hostname
    allow_loopback = settings.is_local_mode()
    candidates: list[str] = []
    try:
        ipaddress.ip_address(host)
        candidates.append(host)
    except ValueError:
        try:
            infos = await asyncio.to_thread(socket.getaddrinfo, host, None)
        except OSError as exc:
            raise ValueError(_PUBLIC_URL_ERROR) from exc
        candidates.extend(cast(str, info[4][0]) for info in infos)
    if not candidates:
        raise ValueError(_PUBLIC_URL_ERROR)
    for addr in candidates:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise ValueError(_PUBLIC_URL_ERROR) from exc
        if ip.is_loopback and allow_loopback:
            continue
        if (
            ip.is_private
            or ip.is_loopback
            or ip.is_link_local
            or ip.is_reserved
            or ip.is_multicast
            or ip.is_unspecified
        ):
            raise ValueError(_PUBLIC_URL_ERROR)


async def _discover_models(
    *,
    url: str,
    headers: dict[str, str],
    parser,
) -> list[DiscoveredModel]:
    await _validate_public_base_url(url)
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(url, headers=headers)
        response.raise_for_status()
    except httpx.HTTPError:
        return []
    try:
        payload = response.json()
    except ValueError:
        return []
    return parser(payload)


def _parse_openai_compatible_models(payload: object) -> list[DiscoveredModel]:
    if not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    models: list[DiscoveredModel] = []
    seen: set[str] = set()
    for item in data:
        model_name: object
        supports_vision = False
        if isinstance(item, dict):
            model_name = item.get("id") or item.get("name")
            supports_vision = _payload_advertises_image_input(item)
        else:
            model_name = item
        if isinstance(model_name, str):
            normalized = model_name.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                models.append(
                    DiscoveredModel(name=normalized, supports_vision=supports_vision)
                )
    return models


def _payload_advertises_image_input(item: dict) -> bool:
    """Best-effort image-input detection from compatible model metadata."""
    architecture = item.get("architecture")
    if not isinstance(architecture, dict):
        return False
    modalities = architecture.get("input_modalities")
    if isinstance(modalities, list) and any(
        isinstance(modality, str) and modality.strip().lower() == "image"
        for modality in modalities
    ):
        return True
    modality = architecture.get("modality")
    return isinstance(modality, str) and "image" in modality.lower()


def _join_url(base_url: str, path: str) -> str:
    return f"{base_url.rstrip('/')}/{path.lstrip('/')}"


def _load_runtime_env() -> None:
    root = Path(__file__).resolve().parents[5]
    backend = Path(__file__).resolve().parents[4]
    load_dotenv(backend / ".env", override=False)
    load_dotenv(root / ".env", override=False)


def _selected_model(
    profile: AgentRuntimeProfile,
    requested_model_name: str | None,
) -> RuntimeModelCatalogEntry | None:
    if profile.runtime_type is RuntimeProfileType.HARNESS:
        return selected_harness_model(profile, requested_model_name)
    model_name = requested_model_name or profile.default_model_name
    if not model_name:
        return None
    for model in profile.model_catalog:
        if model_name == model.name:
            return model
    return None


def _config_dict(config: object | None) -> dict[str, object]:
    if config is None:
        return {}
    model_dump = getattr(config, "model_dump", None)
    if callable(model_dump):
        return _config_dict(model_dump(mode="json"))
    if isinstance(config, dict):
        return config
    return {}
