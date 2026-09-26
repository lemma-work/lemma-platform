"""Talking to a model provider: catalog discovery and its SSRF guard.

Split out of ``runtime_profile_service`` because both creating and editing a
provider profile need it, and neither needs the rest of that module.
"""

from __future__ import annotations

import ipaddress
import socket
from dataclasses import dataclass
from urllib.parse import ParseResult, urlparse

import httpx

from app.core.config import settings
from app.core.exposure import exposure_settings, local_relaxations_allowed
from app.core.log.log import get_logger
from app.core.concurrency.offload import run_blocking
from app.modules.agent.services.context_budget import (
    catalog_metadata_for,
)
from app.modules.agent.domain.runtime_profiles import (
    RuntimeModelCapability,
    RuntimeModelCatalogEntry,
)

logger = get_logger(__name__)


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
    #: The model's context window, when the provider advertises one. Left None
    #: rather than guessed: a wrong window is worse than an admitted unknown,
    #: because compaction is sized from it and a too-large one means the run
    #: does not compact until after the provider has rejected the request.
    context_window: int | None = None


class ProviderKeyRejectedError(ValueError):
    """The provider answered the model listing with 401/403.

    Raised rather than folded into "nothing discovered": a rejected key is the
    commonest mistake when adding a provider, and treating it as an empty list
    saved a profile that read "Available" and then failed every run.
    """


def key_rejected_message(provider_name: str) -> str:
    return f"{provider_name} rejected this API key."


def catalog_unreadable_message(provider_name: str) -> str:
    return (
        f"Couldn't read the model list from {provider_name}. Check the key, "
        "or type a model name below."
    )


def _provider_model_catalog(
    *,
    discovered_models: list[DiscoveredModel],
    fallback_model_names: list[str],
    explicit_vision_model_names: set[str] | None = None,
    default_vision: bool = False,
    provider_name: str = "the provider",
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
    window_by_name: dict[str, int | None] = {}
    order: list[str] = []
    for discovered in discovered_models:
        name = discovered.name.strip()
        if name and name not in vision_by_name:
            order.append(name)
            vision_by_name[name] = discovered.supports_vision
            window_by_name[name] = discovered.context_window
    for model_name in fallback_model_names:
        name = model_name.strip()
        if name and name not in vision_by_name:
            order.append(name)
            vision_by_name[name] = False
    if not order:
        raise ValueError(catalog_unreadable_message(provider_name))
    catalog: list[RuntimeModelCatalogEntry] = []
    for name in order:
        supports_vision = default_vision or vision_by_name[name] or name in explicit
        capabilities = [RuntimeModelCapability.TEXT, RuntimeModelCapability.TOOLS]
        if supports_vision:
            capabilities.append(RuntimeModelCapability.VISION)
        # Recorded where the budget resolver looks for it
        # (`services/context_budget`). Absent when the provider said nothing, so
        # the deployment default applies rather than an invented number.
        catalog.append(
            RuntimeModelCatalogEntry(
                name=name,
                display_name=name,
                provider_model_name=name,
                capabilities=capabilities,
                metadata=catalog_metadata_for(
                    name, discovered_window=window_by_name.get(name)
                ),
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
        url=_anthropic_models_url(base_url),
        headers=request_headers,
        parser=_parse_openai_compatible_models,
    )


def _anthropic_models_url(base_url: str) -> str:
    """Where an Anthropic-compatible route lists its models.

    The base URL is the one the Anthropic SDK takes -- ``https://api.anthropic.com``,
    to which it appends ``/v1/messages`` -- so the listing is ``/v1/models``,
    not ``/models``. A route given with the version already on it is left
    alone rather than doubled.
    """
    trimmed = base_url.rstrip("/")
    if trimmed.endswith("/v1"):
        return f"{trimmed}/models"
    return f"{trimmed}/v1/models"


_PUBLIC_URL_ERROR = "base_url must be a public http(s) URL"
_SHARED_LOOPBACK_ERROR = (
    "Models on this computer can't be added while Lemma is shared: other "
    "people's requests would reach your computer. Turn sharing off to add it."
)


def lemma_service_ports() -> frozenset[int]:
    """The loopback ports this installation's own services listen on.

    A model provider at one of these is not a model provider: it is the API
    (whose own routes then receive the provider's bearer key), the web app,
    Postgres, Redis or SuperTokens, reached from inside the backend. Read from
    the URLs the backend is configured with, so a Desktop install's random
    ports are covered without being named anywhere.
    """
    configured = (
        settings.api_url,
        settings.frontend_url,
        settings.auth_frontend_url,
        settings.supertokens_core_url,
        settings.database_url,
        settings.redis_url,
    )
    return frozenset(port for raw in configured if (port := _port_of(raw)))


def _port_of(raw: str | None) -> int | None:
    try:
        return urlparse(str(raw)).port if raw else None
    except ValueError:
        return None


def _loopback_allowed_for(parsed: ParseResult) -> bool:
    """Local and unshared, and not a port this installation serves on."""
    if not local_relaxations_allowed():
        return False
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError(_PUBLIC_URL_ERROR) from exc
    return port not in lemma_service_ports()


def _refuse_loopback_while_shared(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> None:
    """Say why a model server on this computer is refused, when the reason is
    sharing.

    The one refusal a person can act on: it is their own model server, and
    loopback is closed only while the installation is shared. The generic
    "must be a public URL" read as "your URL is malformed".
    """
    if (
        ip.is_loopback
        and settings.is_local_mode()
        and exposure_settings.installation_shared
    ):
        raise ValueError(_SHARED_LOOPBACK_ERROR)


async def _validate_public_base_url(url: str) -> None:
    """Reject SSRF targets before issuing a server-side request to ``url``.

    A model provider's ``base_url`` is caller-supplied, so block non-http(s)
    schemes and any host that resolves to a loopback/private/link-local/reserved
    address (e.g. ``http://169.254.169.254/`` cloud metadata, ``http://10.x``).
    Loopback is permitted in local/testing mode so development against a model
    server on localhost still works -- but not while the installation is shared,
    when "a member" is anybody who joined over the network, and never on a port
    Lemma itself serves on. (Note: this validates at resolve time; it does not
    pin the connection, so it is not fully DNS-rebinding-proof — it closes the
    practical metadata/internal-service vector.)
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ValueError(_PUBLIC_URL_ERROR)
    host = parsed.hostname
    allow_loopback = _loopback_allowed_for(parsed)
    candidates: list[str] = []
    try:
        ipaddress.ip_address(host)
        candidates.append(host)
    except ValueError:
        try:
            infos = await run_blocking(
                socket.getaddrinfo, host, None, limiter="external_http"
            )
        except OSError as exc:
            raise ValueError(_PUBLIC_URL_ERROR) from exc
        candidates.extend(str(info[4][0]) for info in infos)
    if not candidates:
        raise ValueError(_PUBLIC_URL_ERROR)
    for addr in candidates:
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError as exc:
            raise ValueError(_PUBLIC_URL_ERROR) from exc
        if ip.is_loopback and allow_loopback:
            continue
        _refuse_loopback_while_shared(ip)
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
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code in (401, 403):
            raise ProviderKeyRejectedError(str(exc.response.status_code)) from exc
        return []
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
        context_window: int | None = None
        if isinstance(item, dict):
            model_name = item.get("id") or item.get("name")
            supports_vision = _payload_advertises_image_input(item)
            context_window = _payload_context_window(item)
        else:
            model_name = item
        if isinstance(model_name, str):
            normalized = model_name.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                models.append(
                    DiscoveredModel(
                        name=normalized,
                        supports_vision=supports_vision,
                        context_window=context_window,
                    )
                )
    return models


#: What the field is called across OpenAI-compatible providers. Fireworks and
#: OpenRouter say `context_length`, vLLM says `max_model_len`, others spell it
#: out; OpenRouter also repeats it nested under `top_provider`.
_CONTEXT_WINDOW_KEYS = (
    "context_length",
    "context_window",
    "max_context_length",
    "max_model_len",
)


def _payload_context_window(item: dict) -> int | None:
    """The model's context window from a ``/models`` entry, if it says.

    Best-effort in the same spirit as `_payload_advertises_image_input`: the
    standard OpenAI schema carries no window at all, and returning None there is
    correct. The deployment default then applies, which is safe; a guess would
    not be.
    """
    for key in _CONTEXT_WINDOW_KEYS:
        window = _coerce_positive_int(item.get(key))
        if window is not None:
            return window
    top_provider = item.get("top_provider")
    if isinstance(top_provider, dict):
        return _coerce_positive_int(top_provider.get("context_length"))
    return None


def _coerce_positive_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        window = int(value)
    except TypeError, ValueError:
        return None
    return window if window > 0 else None


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
