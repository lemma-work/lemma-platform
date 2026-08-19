"""Build Pydantic AI models from resolved agent runtime profiles.

The HTTP client behind each provider is pooled per endpoint rather than rebuilt
per run. Building one per agent run meant a fresh connection pool every time —
no keep-alive reuse across a conversation's many model requests, a new TLS
handshake per turn, and a pool that was never closed. Half-open sockets left
behind by that churn are a plausible source of the mid-stream ``ReadError``s the
harness now has to recover from, so this is the other half of that fix.
"""

from __future__ import annotations

import asyncio
import itertools
from collections import OrderedDict
from collections.abc import Mapping
from dataclasses import replace

import httpx
from openai import AsyncOpenAI
from pydantic_ai import UsageLimits
from pydantic_ai.models import Model
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.anthropic import AnthropicProvider
from pydantic_ai.providers.openai import OpenAIProvider
from pydantic_ai.retries import AsyncTenacityTransport, RetryConfig, wait_retry_after
from tenacity import stop_after_attempt, wait_exponential

from app.core.log.log import get_logger
from app.modules.agent.config import agent_settings
from app.modules.agent.infrastructure.transport_errors import (
    RETRYABLE_STATUS_CODES,
)
from app.modules.agent.services.openai_schema_compat import (
    openai_compatible_model_profile,
)

logger = get_logger(__name__)

# Both caches below are bounded. They are keyed by tenant credential, so on a
# multi-tenant deployment their key space grows with the customer list and
# nothing ever removed an entry -- and each provider-client entry pins an
# httpx.AsyncClient with its own connection pool. The cap is generous because
# eviction costs a reconnect, not a correctness problem; it exists so the
# ceiling is a number somebody chose.
_MAX_PROVIDER_CLIENTS = 256

_provider_clients: OrderedDict[str, httpx.AsyncClient] = OrderedDict()

# Closing an evicted client is async, but the accessor that evicts is not, so
# the close is handed to the loop. The set keeps a strong reference until it
# finishes; without one the task can be collected mid-close.
_closing_clients: set[asyncio.Task[None]] = set()


# Opaque per-process labels standing in for credentials in cache keys. The
# first version hashed the key instead, which is the wrong shape twice over: a
# digest of a secret is still derived from the secret (and offline-comparable),
# and the cache only ever needs to know whether two credentials are the *same*,
# never anything about them. A counter answers that exactly, and leaves nothing
# to leak if a key ever reaches a log line or a traceback.
_credential_labels: OrderedDict[str, str] = OrderedDict()

# Monotonic, deliberately not `len(_credential_labels)`. Once the map evicts,
# its length repeats, so a length-derived label would eventually be handed to a
# second, different credential -- and two tenants whose labels collide share a
# client, and therefore an Authorization header. The counter never rewinds.
_credential_sequence = itertools.count(1)


def _credential_label(api_key: str | None) -> str:
    if not api_key:
        return "anonymous"
    label = _credential_labels.get(api_key)
    if label is None:
        label = f"credential-{next(_credential_sequence)}"
        _credential_labels[api_key] = label
        _evict_oldest(_credential_labels, _MAX_PROVIDER_CLIENTS)
    else:
        _credential_labels.move_to_end(api_key)
    return label


def _evict_oldest(cache: OrderedDict, limit: int) -> None:
    while len(cache) > limit:
        cache.popitem(last=False)


def _close_later(client: httpx.AsyncClient) -> None:
    """Close an evicted client on the loop, if there is one to close it on."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (import time, sync tests): nothing to schedule onto
    task = loop.create_task(_aclose_quietly(client))
    _closing_clients.add(task)
    task.add_done_callback(_closing_clients.discard)


async def _aclose_quietly(client: httpx.AsyncClient) -> None:
    try:
        await client.aclose()
    except Exception:  # pragma: no cover - eviction is best-effort
        logger.debug(
            "agent.runtime_model_factory.provider_client_close_failed.diagnostic",
            exc_info=True,
        )


def _client_cache_key(
    protocol: str, base_url: str, api_key: str | None, headers: Mapping[str, object]
) -> str:
    """Identify an endpoint+credential pair without putting the key in the key.

    Two profiles pointing at the same base URL with different credentials must
    not share a client — same endpoint, different tenant.
    """
    header_sig = ",".join(f"{k}={v}" for k, v in sorted(headers.items()))
    return f"{protocol}|{base_url}|{_credential_label(api_key)}|{header_sig}"


def _should_retry_status(response: httpx.Response) -> None:
    """Raise (so tenacity retries) only for statuses that mean "try again".

    Deliberately silent for 400/401/402/404: in production those are a malformed
    request, a bad key, an account out of credit, and a model that doesn't exist.
    Retrying any of them burns the same failure three times and delays the error
    the user actually needs to read.
    """
    if response.status_code in RETRYABLE_STATUS_CODES or response.status_code >= 500:
        response.raise_for_status()


def _build_provider_client(headers: Mapping[str, object]) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        # Split timeouts: the read timeout is per-chunk, so it is the "provider
        # has gone away" threshold rather than a budget for the whole turn — a
        # long tool-using answer streams for minutes without tripping it.
        timeout=httpx.Timeout(
            connect=agent_settings.agent_model_http_connect_timeout_seconds,
            read=agent_settings.agent_model_http_read_timeout_seconds,
            write=30.0,
            pool=10.0,
        ),
        limits=httpx.Limits(
            max_connections=agent_settings.agent_model_http_max_connections,
            max_keepalive_connections=agent_settings.agent_model_http_max_connections,
            keepalive_expiry=30.0,
        ),
        headers={str(key): str(value) for key, value in headers.items()},
        transport=AsyncTenacityTransport(
            config=RetryConfig(
                retry=lambda state: isinstance(
                    state.outcome.exception() if state.outcome else None,
                    (httpx.TransportError, httpx.HTTPStatusError),
                ),
                wait=wait_retry_after(
                    fallback_strategy=wait_exponential(multiplier=1, max=30),
                    max_wait=60,
                ),
                stop=stop_after_attempt(3),
                reraise=True,
            ),
            validate_response=_should_retry_status,
        ),
    )


def get_provider_http_client(
    *, protocol: str, base_url: str, api_key: str | None, headers: Mapping[str, object]
) -> httpx.AsyncClient:
    """The shared client for one provider endpoint, created on first use."""
    key = _client_cache_key(protocol, base_url, api_key, headers)
    client = _provider_clients.get(key)
    if client is None or client.is_closed:
        client = _build_provider_client(headers)
        _provider_clients[key] = client
        while len(_provider_clients) > _MAX_PROVIDER_CLIENTS:
            _, evicted = _provider_clients.popitem(last=False)
            _close_later(evicted)
    else:
        _provider_clients.move_to_end(key)
    return client


async def close_agent_provider_clients() -> None:
    """Close every pooled provider client. Called on application shutdown."""
    clients = list(_provider_clients.values())
    _provider_clients.clear()
    for client in clients:
        try:
            await client.aclose()
        except Exception:  # pragma: no cover - shutdown is best-effort
            logger.debug(
                "agent.runtime_model_factory.provider_client_close_failed.diagnostic",
                exc_info=True,
            )


def pydantic_ai_model_from_runtime_profile(
    *,
    runtime_profile: Mapping[str, object] | None,
    runtime_credentials: Mapping[str, object] | None = None,
    fallback_model_name: str | None = None,
) -> Model | None:
    """Return a Pydantic AI model for model-provider runtime profiles."""
    # e2e mock mode: every model (harness, title generation, any direct
    # agent.run) is the deterministic FunctionModel — no real provider, no key.
    # The harness layers conversation-scripted behaviour on top via its own
    # build_mock_model(conversation); here (no conversation) it's the default.
    from app.modules.agent.infrastructure.harnesses.mock_model import (
        build_mock_model,
        is_mock_llm_enabled,
    )

    if is_mock_llm_enabled():
        return build_mock_model(None)

    if not isinstance(runtime_profile, Mapping):
        return None

    protocol = runtime_profile.get("protocol")
    provider_model_name = runtime_profile.get("provider_model_name")
    model_name_value = (
        provider_model_name
        if isinstance(provider_model_name, str) and provider_model_name
        else fallback_model_name
    )
    if not isinstance(model_name_value, str) or not model_name_value:
        return None

    config = runtime_profile.get("config")
    if not isinstance(config, Mapping):
        return None

    credentials = (
        runtime_credentials if isinstance(runtime_credentials, Mapping) else {}
    )
    api_key = credentials.get("api_key")
    api_key = api_key if isinstance(api_key, str) and api_key else None

    headers = config.get("headers")
    headers = headers if isinstance(headers, Mapping) else {}

    if protocol == "OPENAI_COMPATIBLE":
        base_url = config.get("base_url")
        if not isinstance(base_url, str) or not base_url:
            return None
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "EMPTY",
            # Connection reuse, split timeouts and transport-level retries all
            # come from the shared client; the SDK's own retry layer would only
            # duplicate it.
            http_client=get_provider_http_client(
                protocol="openai",
                base_url=base_url,
                api_key=api_key,
                headers=headers,
            ),
            max_retries=0,
        )
        return OpenAIChatModel(
            model_name_value,
            provider=OpenAIProvider(openai_client=client),
            # Inline `$defs`/`$ref` in tool schemas: some OpenAI-compatible
            # providers (e.g. Fireworks GLM) can't resolve references server-side.
            profile=openai_compatible_model_profile,
        )

    if protocol == "ANTHROPIC_COMPATIBLE":
        base_url = config.get("base_url")
        resolved_base_url = (
            base_url if isinstance(base_url, str) and base_url else None
        )
        provider = AnthropicProvider(
            api_key=api_key,
            base_url=resolved_base_url,
            http_client=get_provider_http_client(
                protocol="anthropic",
                base_url=resolved_base_url or "https://api.anthropic.com",
                api_key=api_key,
                headers=headers,
            ),
        )
        return AnthropicModel(model_name_value, provider=provider)

    return None


def require_pydantic_ai_model_from_runtime_profile(
    *,
    runtime_profile: Mapping[str, object] | None,
    runtime_credentials: Mapping[str, object] | None = None,
    fallback_model_name: str | None = None,
) -> Model:
    """Return a Pydantic AI model or raise a clear profile configuration error."""
    model = pydantic_ai_model_from_runtime_profile(
        runtime_profile=runtime_profile,
        runtime_credentials=runtime_credentials,
        fallback_model_name=fallback_model_name,
    )
    if model is None:
        profile_id = (
            runtime_profile.get("profile_id")
            if isinstance(runtime_profile, Mapping)
            else None
        )
        raise RuntimeError(
            f"Runtime profile {profile_id or '<missing>'!r} cannot build a Pydantic AI model"
        )
    return model


def supports_token_precount(model: Model) -> bool:
    """Whether this model can be asked for a token count before the request.

    ``UsageLimits(count_tokens_before_request=True)`` makes pydantic-ai call
    ``Model.count_tokens`` first. Only some providers implement it; the base
    method raises ``NotImplementedError``, and an OpenAI-compatible chat model —
    which is what the system runtime resolves to — does not override it. Asking
    for the pre-count there does not degrade to counting afterwards, it raises
    and takes the whole call with it.

    The wrapper unwrapping is the load-bearing part. ``WrapperModel`` (and so
    ``InstrumentedModel``, which is applied whenever instrumentation is on, which
    is always in this deployment) *does* override ``count_tokens``, purely to
    delegate. A naive ``type(model).count_tokens is not Model.count_tokens``
    therefore answers "yes, it counts" for every instrumented model and the
    pre-count blows up anyway — the exact shape of the failures this replaced.
    """

    concrete = model
    while isinstance(concrete, WrapperModel):
        concrete = concrete.wrapped
    # `getattr` rather than attribute access because a model here is whatever
    # the profile resolved to, including stand-ins that satisfy only the parts
    # of the protocol their caller uses. Something with no `count_tokens` at all
    # certainly cannot pre-count, and answering that is far better than raising
    # inside a helper whose whole job is to keep a call from raising.
    counter = getattr(type(concrete), "count_tokens", None)
    return counter is not None and counter is not Model.count_tokens


def usage_limits_for(model: Model, limits: UsageLimits) -> UsageLimits:
    """The same caps, pre-counted only when this model can actually do it.

    When it cannot, the caps are enforced against the usage the provider
    reports, so an oversized payload is refused after the call rather than
    before it. That is a weaker guarantee than the caller asked for, and a much
    better one than a helper that raises.
    """

    if limits.count_tokens_before_request and not supports_token_precount(model):
        return replace(limits, count_tokens_before_request=False)
    return limits


async def default_system_runtime():
    """Resolve the code-defined system default runtime profile."""
    from uuid import uuid4

    from app.modules.agent.domain.value_objects import AgentRuntimeConfig
    from app.modules.agent.services.runtime_profile_service import (
        DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID,
        AgentRuntimeProfileService,
    )

    return await AgentRuntimeProfileService().resolve(
        runtime=AgentRuntimeConfig(profile_id=DEFAULT_SYSTEM_AGENT_RUNTIME_PROFILE_ID),
        organization_id=None,
        user_id=uuid4(),
    )
