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

from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from pydantic import HttpUrl, SecretStr

from app.modules.agent.domain.errors import RuntimeProfileValidationError
from app.modules.agent.domain.runtime_profiles import (
    AgentRuntimeProfile,
    ApiKeyRuntimeCredentials,
    RuntimeCredentials,
    refuse_redacted_values,
    reveal_credentials,
)
from app.modules.agent.domain.sentinels import UnsetType


@dataclass(frozen=True, slots=True)
class ProviderProfilePatch:
    """A settled provider edit: what changed, and what has to happen next."""

    changes: dict[str, object]
    base_url: str | HttpUrl | None
    base_url_changed: bool
    origin_changed: bool
    headers: dict[str, str]
    model_settings: dict[str, object]
    credentials: RuntimeCredentials | None
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
    validate_settings: Callable[[dict[str, object] | None], dict[str, object]]
    | None = None,
) -> ProviderProfilePatch:
    """Settle a PATCH against the stored profile.

    ``validate_settings`` refuses model settings a run could not use; the
    caller supplies it, because only the provider adapter knows.
    """
    changes = _label_changes(name=name, description=description)

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
        else _model_settings(model_settings, validate_settings)
    )

    origin_changed = base_url_changed and provider_origin(
        next_base_url, is_anthropic=is_anthropic
    ) != provider_origin(stored.base_url, is_anthropic=is_anthropic)
    if origin_changed:
        _refuse_carrying_secrets(profile, stored, api_key=api_key, headers=headers)

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
        origin_changed=origin_changed,
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


class KeyMustBeReenteredError(RuntimeProfileValidationError):
    """A new provider address with the old key: refused, the key never moves.

    The stored key is write-only -- nobody who can edit a profile can read it
    back. Pointing the profile somewhere else and keeping the key would send it,
    on the very next discovery, run or test, to whatever the editor chose, which
    is the key read back by another route. So a key only ever goes to an
    address it was entered for.
    """

    def __init__(self) -> None:
        super().__init__(
            "The provider address moved to a different host. Re-enter the API "
            "key, and any custom headers, for the new address."
        )


def normalize_profile_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise RuntimeProfileValidationError("Profile name cannot be empty")
    return normalized


def normalize_headers(headers: dict[str, str] | None) -> dict[str, str]:
    refuse_redacted_values(headers, field="headers")
    normalized: dict[str, str] = {}
    for key, value in (headers or {}).items():
        header_name = key.strip()
        header_value = value.strip()
        if header_name and header_value:
            normalized[header_name] = header_value
    return normalized


def _model_settings(
    model_settings: dict[str, object] | None,
    validate: Callable[[dict[str, object] | None], dict[str, object]] | None,
) -> dict[str, object]:
    refuse_redacted_values(model_settings, field="model_settings")
    return (
        validate(model_settings) if validate is not None else dict(model_settings or {})
    )


def _label_changes(*, name, description) -> dict[str, object]:
    changes: dict[str, object] = {}
    if not isinstance(name, UnsetType):
        changes["name"] = normalize_profile_name(name)
    if not isinstance(description, UnsetType):
        changes["description"] = description.strip() if description else None
    return changes


def _refuse_carrying_secrets(
    profile: AgentRuntimeProfile, stored, *, api_key, headers
) -> None:
    """A move to another origin must bring its own key, and its own headers.

    Custom headers count: once the listing shows only their names, a header
    value is as write-only as the key, and as often a credential.
    """
    keeps_key = isinstance(api_key, UnsetType) and profile.credentials is not None
    keeps_headers = isinstance(headers, UnsetType) and bool(stored.headers)
    if keeps_key or keeps_headers:
        raise KeyMustBeReenteredError()


ANTHROPIC_DEFAULT_BASE_URL = "https://api.anthropic.com"

_DEFAULT_PORTS = {"http": 80, "https": 443}


def provider_origin(
    base_url: str | HttpUrl | None, *, is_anthropic: bool
) -> tuple[str, str, int | None]:
    """Scheme, host and port: where a credential sent to ``base_url`` lands.

    The path is not part of it -- moving ``/v1`` to ``/v2`` on the same host
    sends the key to the same party. Anthropic's absent URL is its default
    endpoint, so clearing it is a move like any other.
    """
    raw = str(base_url) if base_url else ""
    if not raw and is_anthropic:
        raw = ANTHROPIC_DEFAULT_BASE_URL
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    return scheme, (parts.hostname or "").lower(), port or _DEFAULT_PORTS.get(scheme)


def _next_base_url(base_url, stored, *, is_anthropic: bool):
    """The base URL after the edit. Null is only meaningful for Anthropic."""
    if isinstance(base_url, UnsetType):
        return stored.base_url
    if base_url is None:
        if not is_anthropic:
            raise RuntimeProfileValidationError(
                "An OpenAI-compatible profile requires a base URL"
            )
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
            raise RuntimeProfileValidationError(
                "An Anthropic-compatible profile requires an API key"
            )
        return None
    return ApiKeyRuntimeCredentials(api_key=SecretStr(str(api_key).strip()))


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
