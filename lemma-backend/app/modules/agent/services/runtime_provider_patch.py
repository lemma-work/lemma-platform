"""What a PATCH to a model provider settled to, before anything is written.

Ten optional fields, each of which can be absent, set, or explicitly null, and
the three mean different things -- absent keeps the stored value, a string
replaces it, null clears it. Getting that wrong on `api_key` silently destroys a
credential, which is why `UNSET` exists at all.

Resolving them is pure: no database, no network, no provider. That is the point
of having it here. The parts of an edit that can fail for reasons outside the
request -- the SSRF check on a new base URL, the provider round trip that
refreshes the model catalog -- happen afterwards, against a patch that has
already been settled, so they can be read and tested separately from it.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import HttpUrl

from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    ApiKeyRuntimeCredentials,
    reveal_credentials,
)
from app.modules.agent.domain.sentinels import UnsetType
from app.modules.agent.services.runtime_profile_creation import (
    normalize_profile_name,
    normalize_headers,
)


@dataclass(frozen=True, slots=True)
class ProviderProfilePatch:
    """A settled provider edit: what changed, and what has to happen next."""

    changes: dict[str, object]
    base_url: str | HttpUrl | None
    base_url_changed: bool
    headers: dict[str, str]
    model_settings: dict[str, object]
    credentials: ApiKeyRuntimeCredentials | None
    rediscover: bool
    config_changed: bool

    def api_secret(self) -> str | None:
        """The plaintext key to authenticate rediscovery with, if there is one."""
        revealed = reveal_credentials(self.credentials) or {}
        secret = revealed.get("api_key")
        return str(secret) if secret else None


def resolve_provider_patch(
    profile: AgentRuntimeProfile,
    stored,
    *,
    is_anthropic: bool,
    name,
    description,
    base_url,
    api_key,
    model_names,
    headers,
    model_settings,
    refresh_models: bool,
) -> ProviderProfilePatch:
    """Settle a PATCH against the stored profile."""
    changes: dict[str, object] = {}
    if not isinstance(name, UnsetType):
        changes["name"] = normalize_profile_name(name)
    if not isinstance(description, UnsetType):
        changes["description"] = description.strip() if description else None

    next_base_url = _next_base_url(base_url, stored, is_anthropic=is_anthropic)
    base_url_changed = not isinstance(base_url, UnsetType) and str(
        next_base_url
    ) != str(stored.base_url)

    next_headers = (
        stored.headers if isinstance(headers, UnsetType) else normalize_headers(headers)
    )
    next_settings = (
        stored.model_settings
        if isinstance(model_settings, UnsetType)
        else (model_settings or {})
    )

    next_credentials = _next_credentials(
        api_key=api_key,
        stored_credentials=profile.credentials,
        is_anthropic=is_anthropic,
    )
    if (
        not isinstance(api_key, UnsetType)
        or next_credentials is not profile.credentials
    ):
        changes["credentials"] = next_credentials

    return ProviderProfilePatch(
        changes=changes,
        base_url=next_base_url,
        base_url_changed=base_url_changed,
        headers=next_headers,
        model_settings=next_settings,
        credentials=next_credentials,
        rediscover=(
            refresh_models
            or base_url_changed
            or not isinstance(api_key, UnsetType)
            or not isinstance(model_names, UnsetType)
        ),
        config_changed=(
            base_url_changed
            or not isinstance(headers, UnsetType)
            or not isinstance(model_settings, UnsetType)
        ),
    )


def _next_base_url(base_url, stored, *, is_anthropic: bool):
    """The base URL after the edit. Null is only meaningful for Anthropic."""
    if isinstance(base_url, UnsetType):
        return stored.base_url
    if base_url is None:
        if not is_anthropic:
            raise ValueError("An OpenAI-compatible profile requires a base URL")
        return None
    return base_url


def _next_credentials(*, api_key, stored_credentials, is_anthropic: bool):
    """Absent keeps the stored key, a string rotates it, an explicit null clears it.

    Clearing is refused for Anthropic, which has no unauthenticated mode: a
    profile without a key there is one that fails at the next run rather than
    at the edit that broke it.
    """
    if isinstance(api_key, UnsetType):
        return stored_credentials
    if api_key is None or not str(api_key).strip():
        if is_anthropic:
            raise ValueError("An Anthropic-compatible profile requires an API key")
        return None
    return ApiKeyRuntimeCredentials(api_key=str(api_key).strip())


def resolve_catalog_names(profile, model_names, discovered) -> list[str]:
    """Which model names to seed the rebuilt catalog with.

    Explicit names win. Otherwise an empty list, because
    `_provider_model_catalog` unions its fallback with what it discovered -- so
    passing the stored names would keep a model the provider has dropped
    selectable forever. Unless discovery came back empty, which is a brief
    outage or a provider with no `/models` endpoint: then the working catalog is
    better than a blank one.
    """
    if not isinstance(model_names, UnsetType):
        return list(model_names)
    if discovered:
        return []
    return [entry.name for entry in profile.model_catalog]
