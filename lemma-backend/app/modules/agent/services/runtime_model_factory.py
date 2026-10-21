"""Build Pydantic AI models from resolved agent runtime profiles.

The HTTP client behind each provider is pooled per endpoint rather than rebuilt
per run. Building one per agent run meant a fresh connection pool every time —
no keep-alive reuse across a conversation's many model requests, a new TLS
handshake per turn, and a pool that was never closed. Half-open sockets left
behind by that churn are a plausible source of the mid-stream ``ReadError``s the
harness now has to recover from, so this is the other half of that fix.

The pool is an ``httpx2`` client, not an ``httpx`` one: both provider SDKs are
built on ``httpx2``, and the anthropic SDK refuses an ``httpx`` client outright.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from http.cookiejar import CookieJar, DefaultCookiePolicy
from dataclasses import replace

import httpx2
from openai import AsyncOpenAI
from openai.types.chat import ChatCompletionStreamOptionsParam
from pydantic import ConfigDict, TypeAdapter, with_config
from pydantic_ai import UsageLimits
from pydantic_ai.settings import ModelSettings
from pydantic_ai.models import Model
from pydantic_ai.models.wrapper import WrapperModel
from pydantic_ai.models.openai import OpenAIChatModel, OpenAIChatModelSettings
from pydantic_ai.providers.openai import OpenAIProvider

from app.core.bounded import BoundedDict
from app.core.log.log import get_logger
from app.modules.agent.config import agent_settings
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.services.model_stream_budget import (
    ModelStreamBudgetTransport,
)
from app.modules.agent.services.openai_schema_compat import (
    openai_compatible_model_profile,
)

logger = get_logger(__name__)

# Bounded. Keyed per runtime profile, so on a multi-tenant deployment the key
# space grows with the customer list and nothing ever removed an entry -- and
# each entry pins an httpx2.AsyncClient with its own connection pool. The cap is
# generous because eviction costs a reconnect, not a correctness problem; it
# exists so the ceiling is a number somebody chose.
_MAX_PROVIDER_CLIENTS = 256

# The output cap an Anthropic-compatible request carries when its caller set
# none. pydantic-ai used to default to this; later 2.x releases send the model's
# maximum output instead (16384 for a name it does not recognise), which for a
# model behind an operator's gateway can be far more than the call was written
# for. Agent runs and the connection check always set their own cap, so this
# holds only for the one-shot calls that do not -- vision descriptions and
# titles -- and keeps them where they were. Request settings override it.
ANTHROPIC_DEFAULT_MAX_TOKENS = 4096

_provider_clients: BoundedDict[str, httpx2.AsyncClient] = BoundedDict(
    _MAX_PROVIDER_CLIENTS,
    name="agent.provider_clients",
    touch_on_get=True,
    on_evict=lambda _key, client: _close_later(client),
)

# Closing an evicted client is async, but the accessor that evicts is not, so
# the close is handed to the loop. The set keeps a strong reference until it
# finishes; without one the task can be collected mid-close.
_closing_clients: set[asyncio.Task[None]] = set()


@with_config(ConfigDict(arbitrary_types_allowed=True, extra="allow"))
class ProviderModelSettings(ModelSettings):
    """Validate shared model settings while preserving provider extension keys."""


_PROVIDER_SETTINGS_ADAPTER = TypeAdapter(ProviderModelSettings)


def provider_model_settings(
    settings: Mapping[str, object] | None,
) -> ModelSettings | None:
    return (
        _PROVIDER_SETTINGS_ADAPTER.validate_python(settings)
        if settings is not None
        else None
    )


def _close_later(client: httpx2.AsyncClient) -> None:
    """Close an evicted client on the loop, if there is one to close it on."""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return  # no loop (import time, sync tests): nothing to schedule onto
    task = loop.create_task(_aclose_quietly(client))
    _closing_clients.add(task)
    task.add_done_callback(_closing_clients.discard)


async def _aclose_quietly(client: httpx2.AsyncClient) -> None:
    try:
        await client.aclose()
    except Exception:  # pragma: no cover - eviction is best-effort
        logger.warning(
            "agent.runtime_model_factory.provider_client_close_failed.degraded",
            exc_info=True,
        )


def _client_cache_key(protocol: str, base_url: str, profile_id: str | None) -> str:
    """One pool per endpoint per runtime profile, named by nothing secret.

    The pooled client carries no credential: the API key and the profile's
    headers ride on the SDK client built over it, request by request, and the
    pool keeps no cookies. So a key needs no credential in it, nor anything
    derived from one -- a digest of a secret is still the secret's, to anyone
    who can guess candidates. The profile keeps one tenant's traffic in its own
    pool, so a busy tenant cannot take every connection to a shared endpoint.
    """
    return f"{protocol}|{base_url}|{profile_id or 'unprofiled'}"


def _no_cookies() -> CookieJar:
    """A jar that stores nothing, so a pool shared across runs shares no state.

    A provider or the gateway in front of it may set cookies, and httpx2 keeps
    them for the client's life and sends them on every later request -- from
    whichever run uses the pool next.
    """
    return CookieJar(policy=DefaultCookiePolicy(allowed_domains=[]))


def _build_provider_client() -> httpx2.AsyncClient:
    # Limits belong to the transport, not to the client. `AsyncClient._init_transport`
    # returns a transport passed to it *before* it ever looks at `limits`, so a
    # `limits=` beside a custom `transport=` is accepted and silently discarded:
    # this pool ran on the library's defaults (20 keepalive, 5s expiry) while the
    # settings said 100 and 30, and `agent_model_http_max_connections` did
    # nothing at all. Building the base transport here is what makes the setting
    # real. Retries belong to the metered model boundary so each dispatch
    # receives spending authority before reaching this transport.
    pooled = httpx2.AsyncHTTPTransport(
        limits=httpx2.Limits(
            max_connections=agent_settings.agent_model_http_max_connections,
            max_keepalive_connections=agent_settings.agent_model_http_max_connections,
            keepalive_expiry=30.0,
        ),
    )
    return httpx2.AsyncClient(
        # Split timeouts: the read timeout is per-chunk, so it is the "provider
        # has gone away" threshold rather than a budget for the whole turn — a
        # long tool-using answer streams for minutes without tripping it. What
        # it therefore cannot see is a provider that keeps sending *something*
        # forever, which is what the stream budget outside it is for.
        timeout=httpx2.Timeout(
            connect=agent_settings.agent_model_http_connect_timeout_seconds,
            read=agent_settings.agent_model_http_read_timeout_seconds,
            write=30.0,
            pool=10.0,
        ),
        cookies=_no_cookies(),
        transport=ModelStreamBudgetTransport(
            pooled,
            first_chunk_seconds=(
                agent_settings.agent_model_stream_first_chunk_timeout_seconds
            ),
            total_seconds=agent_settings.agent_model_stream_total_timeout_seconds,
        ),
    )


def get_provider_http_client(
    *, protocol: str, base_url: str, profile_id: str | None
) -> httpx2.AsyncClient:
    """The shared client for one provider endpoint, created on first use.

    It holds no credential and no header: pass those to the SDK client built
    over it, which sends them with each request.
    """
    key = _client_cache_key(protocol, base_url, profile_id)
    client = _provider_clients.get(key)
    if client is None or client.is_closed:
        client = _build_provider_client()
        _provider_clients[key] = client
    return client


async def close_agent_provider_clients() -> None:
    """Close every pooled provider client. Called on application shutdown."""
    clients = list(_provider_clients.values())
    _provider_clients.clear()
    for client in clients:
        try:
            await client.aclose()
        except Exception:  # pragma: no cover - shutdown is best-effort
            logger.warning(
                "agent.runtime_model_factory.provider_client_close_failed.degraded",
                exc_info=True,
            )


class _UsageOnlyStreamOptionsChatModel(OpenAIChatModel):
    """An OpenAI-compatible chat model that keeps `stream_options` to the spec.

    pydantic-ai's ``openai_continuous_usage_stats`` does two unrelated things:
    it puts ``continuous_usage_stats`` in the request's ``stream_options``, and
    it switches the streaming handler from summing each chunk's usage to
    replacing with it. Only the second is wanted here, and the two have to be
    separated because they are not equally portable.

    The replace half is what keeps a provider that reports a running cumulative
    total on every chunk from being re-added on top of itself; without it a
    single turn bills a multiple of what it used. The request half is a vLLM
    extension rather than OpenAI's -- the openai SDK's own stream-options type
    does not carry the field -- so a strict OpenAI-compatible endpoint rejects
    the whole request for an unknown field. Models behind one provider disagree
    about accepting it, and this profile fronts whatever endpoint an operator
    points it at, so sending it makes streaming fail for some of them.

    Dropping it costs nothing: a provider that sends cumulative usage does so on
    its own, and the field cannot turn that off. So the setting stays on the
    profile for the accumulation it selects, and never reaches the wire.
    """

    def _get_stream_options(
        self, model_settings: OpenAIChatModelSettings
    ) -> ChatCompletionStreamOptionsParam:
        return {"include_usage": True}


def _nonempty_str(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _provider_access(
    runtime_profile: Mapping[str, object],
    config: Mapping[str, object],
    runtime_credentials: Mapping[str, object] | None,
) -> tuple[str | None, dict[str, str] | None, str | None]:
    """The key and headers the SDK client sends, and the profile it pools under."""
    credentials = (
        runtime_credentials if isinstance(runtime_credentials, Mapping) else {}
    )
    headers = config.get("headers")
    default_headers = (
        {str(key): str(value) for key, value in headers.items()}
        if isinstance(headers, Mapping)
        else None
    )
    return (
        _nonempty_str(credentials.get("api_key")),
        default_headers or None,
        _nonempty_str(runtime_profile.get("profile_id")),
    )


def _unmetered_model_from_runtime_profile(
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

    api_key, default_headers, profile_id = _provider_access(
        runtime_profile, config, runtime_credentials
    )

    if protocol == RuntimeProfileProtocol.OPENAI_COMPATIBLE:
        base_url = config.get("base_url")
        if not isinstance(base_url, str) or not base_url:
            return None
        client = AsyncOpenAI(
            base_url=base_url,
            api_key=api_key or "EMPTY",
            default_headers=default_headers,
            # Disable SDK retries: each attempt must pass through metering.
            http_client=get_provider_http_client(
                protocol="openai", base_url=base_url, profile_id=profile_id
            ),
            max_retries=0,
        )
        return _UsageOnlyStreamOptionsChatModel(
            model_name_value,
            provider=OpenAIProvider(openai_client=client),
            # Inline `$defs`/`$ref` in tool schemas: some OpenAI-compatible
            # providers (e.g. Fireworks GLM) can't resolve references server-side.
            profile=openai_compatible_model_profile,
        )

    if protocol == RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE:
        from pydantic_ai.providers.anthropic import AnthropicProvider
        from anthropic import AsyncAnthropic

        from app.modules.agent.services.anthropic_thinking import (
            AdaptiveThinkingAnthropicModel,
        )

        base_url = config.get("base_url")
        resolved_base_url = base_url if isinstance(base_url, str) and base_url else None
        provider = AnthropicProvider(
            anthropic_client=AsyncAnthropic(
                max_retries=0,
                api_key=api_key,
                base_url=resolved_base_url,
                default_headers=default_headers,
                http_client=get_provider_http_client(
                    protocol="anthropic",
                    base_url=resolved_base_url or "https://api.anthropic.com",
                    profile_id=profile_id,
                ),
            )
        )
        return AdaptiveThinkingAnthropicModel(
            model_name_value,
            provider=provider,
            settings=ModelSettings(max_tokens=ANTHROPIC_DEFAULT_MAX_TOKENS),
        )

    return None


def connection_check_model(
    *,
    runtime_profile: Mapping[str, object],
    runtime_credentials: Mapping[str, object] | None,
) -> Model | None:
    """The model a Models-page connection test sends its one message to.

    Unmetered on purpose: a 16-token "are you there" that an admin asked for is
    not a teammate's usage, and metering needs a run to attribute it to.
    """
    return _unmetered_model_from_runtime_profile(
        runtime_profile=runtime_profile,
        runtime_credentials=runtime_credentials,
    )


def pydantic_ai_model_from_runtime_profile(
    *,
    runtime_profile: Mapping[str, object] | None,
    runtime_credentials: Mapping[str, object] | None = None,
    fallback_model_name: str | None = None,
) -> Model | None:
    from app.modules.usage.contracts.metering import meter_model

    model = _unmetered_model_from_runtime_profile(
        runtime_profile=runtime_profile,
        runtime_credentials=runtime_credentials,
        fallback_model_name=fallback_model_name,
    )
    if model is None or runtime_profile is None:
        return model
    return meter_model(model, runtime_profile)


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
