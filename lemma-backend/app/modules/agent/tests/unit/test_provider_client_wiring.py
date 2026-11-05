"""What the provider client is actually built with, as opposed to what it says.

Both of these pin a setting that was being written down and then discarded, and
neither failure was visible from anywhere: the client was constructed without
error, served traffic, and used values nobody had chosen.

``limits=`` is the sharper one. ``AsyncClient._init_transport`` returns a
transport handed to it *before* it looks at ``limits``, so passing both is
accepted in silence — the pool ran on the library's defaults (20 keepalive, 5s expiry)
while the settings said 100 and 30, and ``agent_model_http_max_connections`` did
nothing whatsoever. A test that reads the pool is the only way that shows up,
because every test that reads the *settings* agreed with itself.
"""

from __future__ import annotations

import httpcore2
import httpx2
import pytest
from anthropic import AsyncAnthropic
from openai import AsyncOpenAI
from pydantic_ai.models import ModelRequestParameters
from pydantic_ai.models.anthropic import AnthropicModel
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.settings import ModelSettings

from app.modules.agent.config import agent_settings
from app.modules.agent.domain.runtime_profiles import RuntimeProfileProtocol
from app.modules.agent.infrastructure.harness.lemma import mock_model
from app.modules.agent.infrastructure.providers import model_factory as factory
from app.modules.agent.infrastructure.providers.model_stream_budget import (
    ModelStreamBudgetTransport,
)


@pytest.fixture
def client() -> httpx2.AsyncClient:
    return factory._build_provider_client()


def _pool(client: httpx2.AsyncClient) -> httpcore2.AsyncConnectionPool:
    """Walk down to the connection pool the requests actually go through."""
    transport = client._transport
    assert isinstance(transport, ModelStreamBudgetTransport)
    assert isinstance(transport.wrapped, httpx2.AsyncHTTPTransport)
    return transport.wrapped._pool


def test_the_configured_pool_ceiling_reaches_the_pool(
    client: httpx2.AsyncClient,
) -> None:
    """The setting was inert: read it off the live pool, not off the config."""
    assert _pool(client)._max_connections == (
        agent_settings.agent_model_http_max_connections
    )


def test_the_configured_keepalive_reaches_the_pool(client: httpx2.AsyncClient) -> None:
    """Keepalive was the library's 20/5s, not the 100/30s written beside it.

    Worth its own test rather than an extra assertion above: the ceiling
    happened to equal the library's default, so it would have passed while wrong.
    Keepalive is where the two genuinely differ, and so where the bug shows.
    """
    pool = _pool(client)
    assert pool._max_keepalive_connections == (
        agent_settings.agent_model_http_max_connections
    )
    assert pool._keepalive_expiry == 30.0


def test_the_stream_budget_covers_the_provider_response(
    client: httpx2.AsyncClient,
) -> None:
    transport = client._transport
    assert isinstance(transport, ModelStreamBudgetTransport)
    assert transport._total_seconds == (
        agent_settings.agent_model_stream_total_timeout_seconds
    )
    assert transport._first_chunk_seconds == (
        agent_settings.agent_model_stream_first_chunk_timeout_seconds
    )


def test_the_per_chunk_read_timeout_still_applies(client: httpx2.AsyncClient) -> None:
    """The budget adds a bound; it does not replace the one already there.

    A custom transport does *not* discard timeouts the way it discards limits —
    they travel per-request in the extensions — so this stays true, and it is
    worth pinning next to the limits bug so the two are not conflated later.
    """
    assert client.timeout.read == agent_settings.agent_model_http_read_timeout_seconds
    assert client.timeout.connect == (
        agent_settings.agent_model_http_connect_timeout_seconds
    )


def test_the_anthropic_sdk_accepts_the_pooled_client() -> None:
    """anthropic 1.x refuses an `httpx` client with a `TypeError` at construction.

    So a factory that built the pool on the wrong stack failed every run on an
    Anthropic-compatible profile, before a single request was sent.
    """
    pooled = factory.get_provider_http_client(
        protocol="anthropic",
        base_url="https://api.anthropic.example.test",
        profile_id="profile-wiring",
    )

    sdk = AsyncAnthropic(api_key="test", http_client=pooled, max_retries=0)

    assert sdk._client is pooled


def test_the_openai_sdk_accepts_the_pooled_client() -> None:
    pooled = factory.get_provider_http_client(
        protocol="openai",
        base_url="https://openai.example.test/v1",
        profile_id="profile-wiring",
    )

    sdk = AsyncOpenAI(api_key="test", http_client=pooled, max_retries=0)

    assert sdk._client is pooled


@pytest.mark.parametrize(
    ("protocol", "pool_protocol", "base_url"),
    [
        (
            RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
            "anthropic",
            "https://api.anthropic.example.test",
        ),
        (
            RuntimeProfileProtocol.OPENAI_COMPATIBLE,
            "openai",
            "https://openai.example.test/v1",
        ),
    ],
)
def test_the_factory_builds_each_sdk_over_the_pooled_client(
    monkeypatch: pytest.MonkeyPatch,
    protocol: RuntimeProfileProtocol,
    pool_protocol: str,
    base_url: str,
) -> None:
    """The whole factory path, not just the SDK constructor, for each protocol."""
    monkeypatch.setattr(mock_model, "is_mock_llm_enabled", lambda: False)

    model = factory.connection_check_model(
        runtime_profile={
            "protocol": protocol,
            "provider_model_name": "a-1",
            "profile_id": "profile-wiring",
            "config": {"base_url": base_url},
        },
        runtime_credentials={"api_key": "test"},
    )

    assert isinstance(model, (AnthropicModel, OpenAIChatModel))
    assert model.client._client is factory.get_provider_http_client(
        protocol=pool_protocol, base_url=base_url, profile_id="profile-wiring"
    )


def test_an_anthropic_request_with_no_cap_keeps_the_old_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """pydantic-ai stopped defaulting to 4096 and now sends the model's maximum.

    Vision and title calls set no cap of their own, so without a model-level
    default they would ask a gateway model for its whole output range. A
    request's own `max_tokens` still wins: model settings sit under it.
    """
    monkeypatch.setattr(mock_model, "is_mock_llm_enabled", lambda: False)

    model = factory.connection_check_model(
        runtime_profile={
            "protocol": RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE,
            "provider_model_name": "a-1",
            "profile_id": "profile-wiring",
            "config": {"base_url": "https://api.anthropic.example.test"},
        },
        runtime_credentials={"api_key": "test"},
    )

    assert isinstance(model, AnthropicModel)
    assert model.settings == {"max_tokens": factory.ANTHROPIC_DEFAULT_MAX_TOKENS}
    unset, _ = model.prepare_request(None, ModelRequestParameters())
    assert unset is not None
    assert unset.get("max_tokens") == factory.ANTHROPIC_DEFAULT_MAX_TOKENS
    explicit, _ = model.prepare_request(
        ModelSettings(max_tokens=123), ModelRequestParameters()
    )
    assert explicit is not None and explicit.get("max_tokens") == 123
