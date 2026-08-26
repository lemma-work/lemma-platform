"""The runtime profiles LEMMA ships with, and the model catalogs behind them.

A "system" profile is one nobody created: it exists because the deployment has
credentials in its environment, so `system:lemma` appears in every pod without a
row anywhere. That makes this the deployment's front door -- what these
functions read out of the environment is exactly what a new pod can talk to on
day one.

The catalog is customizable at runtime (`register_system_openai_catalog_customizer`)
because a deployment can front a gateway that exposes a different model list
than the environment names, and the display names have to follow.

Split from the profile service because none of it touches the database: it is
configuration read into value objects, and the service is storage.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from pydantic import HttpUrl, SecretStr

from app.core.config import reveal_secret, settings
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
)

SYSTEM_LEMMA_PROFILE_ID = "system:lemma"
DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID = SYSTEM_LEMMA_PROFILE_ID

SystemOpenAICatalogCustomizer = Callable[
    [list[RuntimeModelCatalogEntry]], list[RuntimeModelCatalogEntry]
]

_system_openai_catalog_customizer: SystemOpenAICatalogCustomizer | None = None


def _load_runtime_env() -> None:
    root = Path(__file__).resolve().parents[5]
    backend = Path(__file__).resolve().parents[4]
    load_dotenv(backend / ".env", override=False)
    load_dotenv(root / ".env", override=False)


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


def system_lemma_profile() -> AgentRuntimeProfile | None:
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
            base_url=HttpUrl(
                os.getenv("LEMMA_ANTHROPIC_BASE_URL")
                or settings.lemma_anthropic_base_url
            ),
        ),
        credentials=ApiKeyRuntimeCredentials(api_key=SecretStr(api_key)),
    )


def system_profile_by_id(profile_id: str) -> AgentRuntimeProfile | None:
    if profile_id == SYSTEM_LEMMA_PROFILE_ID:
        return system_lemma_profile()
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


def agent_host_model_catalog(
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
