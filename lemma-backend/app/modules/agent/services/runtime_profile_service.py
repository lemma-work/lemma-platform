"""Agent runtime profile listing and resolution."""

from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID, uuid4

import httpx
from dotenv import load_dotenv
from pydantic import HttpUrl

from app.core.config import reveal_secret, settings
from app.core.domain.errors import DomainError
from app.modules.agent.domain.runtime_profiles import (
    AnthropicCompatibleRuntimeConfig,
    AgentRuntimeProfile,
    ApiKeyRuntimeCredentials,
    OpenAICompatibleRuntimeConfig,
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
    RuntimeProfileKind,
    RuntimeProfileProtocol,
    RuntimeProfileScope,
    RuntimeProfileStatus,
    reveal_credentials,
)
from app.modules.agent.domain.value_objects import AgentRuntimeConfig, HarnessKind
from app.modules.agent.infrastructure.repositories import (
    AgentRuntimeDaemonRepository,
    AgentRuntimeProfileRepository,
)

SYSTEM_LEMMA_PROFILE_ID = "system:lemma"
DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID = SYSTEM_LEMMA_PROFILE_ID


@dataclass(frozen=True, slots=True)
class DiscoveredModel:
    """A model returned by a provider's ``/models`` endpoint.

    ``supports_vision`` is best-effort: it is ``True`` only when the provider
    advertises image input for the model (OpenRouter-style
    ``architecture.input_modalities``). Most OpenAI-compatible ``/models``
    payloads carry no modality data, so this stays ``False`` and vision must be
    declared via configuration instead.
    """

    name: str
    supports_vision: bool = False


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


def _build_system_openai_catalog() -> list[RuntimeModelCatalogEntry]:
    """The env-configured system OpenAI catalog, after any registered customizer.

    Shared by the live profile and the pricing-coverage names so both reflect the
    same (optionally remapped) catalog. Requires no credentials, so it can run at
    import/startup.
    """
    _load_runtime_env()
    model_names = _csv_setting(
        os.getenv("LEMMA_OPENAI_MODEL_NAMES") or settings.lemma_openai_model_names
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
    """``(public_name, provider_model_name)`` for the configured system:lemma
    OpenAI-compatible catalog.

    Mirrors the catalog built by ``_system_lemma_openai_profile`` (including any
    registered customizer) without requiring credentials, so it can drive the
    usage-pricing coverage invariant at import/startup.
    """
    return [
        (entry.name, entry.provider_model_name)
        for entry in _build_system_openai_catalog()
    ]


def _openai_compat_model_capabilities(
    model_name: str,
    vision_model_names: set[str],
) -> list[RuntimeModelCapability]:
    capabilities = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
    if model_name in vision_model_names:
        capabilities.append(RuntimeModelCapability.VISION)
    return capabilities

USER_DAEMON_PROFILE_PROTOCOLS = {
    HarnessKind.CODEX: RuntimeProfileProtocol.CODEX_APP_SERVER,
    HarnessKind.CLAUDE_CODE: RuntimeProfileProtocol.CLAUDE_CODE,
    HarnessKind.OPENCODE: RuntimeProfileProtocol.OPENCODE,
}


@dataclass(slots=True)
class ResolvedAgentRuntime:
    profile: AgentRuntimeProfile
    harness_kind: HarnessKind
    model: RuntimeModelCatalogEntry | None
    provider_model_name: str | None
    credentials: dict[str, object] | None

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
            "daemon_id": str(self.profile.daemon_id) if self.profile.daemon_id else None,
            "scope": self.profile.scope.value,
            "protocol": self.profile.protocol.value,
            "model_name": self.model.name if self.model else None,
            "provider_model_name": self.provider_model_name,
            "config": _config_dict(self.profile.config),
        }


class AgentRuntimeProfileService:
    """List and resolve runtime profiles available to a user/org."""

    def __init__(
        self,
        repository: AgentRuntimeProfileRepository | None = None,
        daemon_repository: AgentRuntimeDaemonRepository | None = None,
    ):
        self.repository = repository
        self.daemon_repository = daemon_repository

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

    async def create_user_daemon_profile(
        self,
        *,
        organization_id: UUID,
        user_id: UUID,
        daemon_id: UUID,
        harness_kind: HarnessKind,
        name: str,
        scope: RuntimeProfileScope = RuntimeProfileScope.ORGANIZATION,
        description: str | None = None,
        default_model_name: str | None = None,
    ) -> AgentRuntimeProfile:
        if self.repository is None:
            raise RuntimeError("Runtime profile repository is required")
        if self.daemon_repository is None:
            raise RuntimeError("Runtime daemon repository is required")
        if harness_kind not in USER_DAEMON_PROFILE_PROTOCOLS:
            raise ValueError("Unsupported user daemon harness kind")
        if scope not in {RuntimeProfileScope.ORGANIZATION, RuntimeProfileScope.PERSONAL}:
            raise ValueError("User daemon profile scope must be ORGANIZATION or PERSONAL")

        normalized_name = name.strip()
        if not normalized_name:
            raise ValueError("Profile name cannot be empty")

        daemon = await self.daemon_repository.get_for_user(
            daemon_id=daemon_id,
            user_id=user_id,
        )
        if daemon is None:
            raise ValueError("Daemon is not available for the current user")

        detected_models = _daemon_harness_model_names(
            harness_catalog=getattr(daemon, "harness_catalog", {}) or {},
            harness_kind=harness_kind,
        )
        if detected_models is None:
            raise ValueError(
                f"{harness_kind.value} is not available from daemon {daemon_id}"
            )
        model_names = _user_daemon_model_names(detected_models)
        selected_default_model = _select_user_daemon_default_model(
            requested_model_name=default_model_name,
            model_names=model_names,
        )
        profile = AgentRuntimeProfile(
            id=str(uuid4()),
            organization_id=organization_id,
            user_id=user_id,
            daemon_id=daemon_id,
            scope=scope,
            kind=RuntimeProfileKind.HARNESS,
            protocol=USER_DAEMON_PROFILE_PROTOCOLS[harness_kind],
            name=normalized_name,
            description=description.strip() if description else None,
            default_model_name=selected_default_model,
            model_catalog=[
                RuntimeModelCatalogEntry(
                    name=model_name,
                    display_name=model_name,
                    provider_model_name=model_name,
                    capabilities=[
                        RuntimeModelCapability.TEXT,
                        RuntimeModelCapability.TOOLS,
                    ],
                )
                for model_name in model_names
            ],
            config={},
            status=RuntimeProfileStatus.ACTIVE,
            metadata={
                "source": "USER_DAEMON",
            },
        )
        return await self.repository.create(profile)

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
            raise RuntimeError(f"Agent runtime profile {profile_id!r} is not available")
        model = _selected_model(profile, runtime.model_name)
        if model is None:
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
        os.getenv("LEMMA_ANTHROPIC_MODEL_NAMES")
        or settings.lemma_anthropic_model_names
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
    model_names: list[str] = []
    for raw_model_name in value.split(","):
        model_name = raw_model_name.strip()
        if model_name and model_name not in model_names:
            model_names.append(model_name)
    if not model_names:
        raise RuntimeError("Lemma system model profile requires at least one model")
    return model_names


def _display_model_name(model_name: str) -> str:
    return model_name.replace("-", " ").replace("_", " ").title()


def _user_daemon_model_names(detected_models: list[str]) -> list[str]:
    model_names = ["default"]
    for model_name in detected_models:
        normalized = model_name.strip()
        if normalized and normalized not in model_names:
            model_names.append(normalized)
    return model_names


def _daemon_harness_model_names(
    *,
    harness_catalog: object,
    harness_kind: HarnessKind,
) -> list[str] | None:
    if not isinstance(harness_catalog, dict):
        return None
    entry = harness_catalog.get(harness_kind.value)
    if not isinstance(entry, dict):
        return None
    if entry.get("available") is False:
        return None
    raw_models = entry.get("models")
    if not isinstance(raw_models, list):
        return []
    return [model for model in raw_models if isinstance(model, str)]


def _select_user_daemon_default_model(
    *,
    requested_model_name: str | None,
    model_names: list[str],
) -> str:
    if requested_model_name is None:
        return "default"
    normalized = requested_model_name.strip()
    if normalized not in model_names:
        raise ValueError(
            "default_model_name must be one of the detected model names"
        )
    return normalized


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
        raise ValueError(
            "default_model_name must be one of the provider model names"
        )
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
        candidates.extend(info[4][0] for info in infos)
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
    """Best-effort image-input detection from an OpenAI-compatible ``/models``
    entry. Honors OpenRouter-style ``architecture.input_modalities`` /
    ``architecture.modality``; absent that metadata (the standard OpenAI schema),
    returns ``False`` so vision falls back to explicit configuration.
    """
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
    model_name = requested_model_name or profile.default_model_name
    if not model_name:
        return None
    for model in profile.model_catalog:
        if model_name == model.name:
            return model
    if requested_model_name:
        raise RuntimeError(
            f"Model {requested_model_name!r} is not in runtime profile {profile.id!r}"
        )
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
