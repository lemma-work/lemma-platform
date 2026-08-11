"""Build Pydantic AI models from resolved agent runtime profiles.

The HTTP client behind each provider is pooled per endpoint rather than rebuilt
per run. Building one per agent run meant a fresh connection pool every time —
no keep-alive reuse across a conversation's many model requests, a new TLS
handshake per turn, and a pool that was never closed. Half-open sockets left
behind by that churn are a plausible source of the mid-stream ``ReadError``s the
harness now has to recover from, so this is the other half of that fix.
"""

from __future__ import annotations

from collections.abc import Mapping

import httpx
from openai import AsyncOpenAI
from pydantic_ai.models import Model
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

_provider_clients: dict[str, httpx.AsyncClient] = {}


# Opaque per-process labels standing in for credentials in cache keys. The
# first version hashed the key instead, which is the wrong shape twice over: a
# digest of a secret is still derived from the secret (and offline-comparable),
# and the cache only ever needs to know whether two credentials are the *same*,
# never anything about them. A counter answers that exactly, and leaves nothing
# to leak if a key ever reaches a log line or a traceback.
_credential_labels: dict[str, str] = {}


def _credential_label(api_key: str | None) -> str:
    if not api_key:
        return "anonymous"
    label = _credential_labels.get(api_key)
    if label is None:
        label = f"credential-{len(_credential_labels) + 1}"
        _credential_labels[api_key] = label
    return label


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


async def default_system_pydantic_ai_model() -> Model:
    """Build the code-defined system default profile model."""
    resolved = await default_system_runtime()
    return require_pydantic_ai_model_from_runtime_profile(
        runtime_profile=resolved.public_snapshot(),
        runtime_credentials=resolved.credentials or {},
        fallback_model_name=resolved.model_name_for_harness,
    )


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
