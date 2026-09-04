"""What the provider client is actually built with, as opposed to what it says.

Both of these pin a setting that was being written down and then discarded, and
neither failure was visible from anywhere: the client was constructed without
error, served traffic, and used values nobody had chosen.

``limits=`` is the sharper one. ``AsyncClient._init_transport`` returns a
transport handed to it *before* it looks at ``limits``, so passing both is
accepted in silence — the pool ran on httpx's defaults (20 keepalive, 5s expiry)
while the settings said 100 and 30, and ``agent_model_http_max_connections`` did
nothing whatsoever. A test that reads the pool is the only way that shows up,
because every test that reads the *settings* agreed with itself.
"""

from __future__ import annotations

import httpx
import pytest

from app.modules.agent.config import agent_settings
from app.modules.agent.services import runtime_model_factory as factory
from app.modules.agent.services.model_stream_budget import ModelStreamBudgetTransport


@pytest.fixture
def client() -> httpx.AsyncClient:
    return factory._build_provider_client({})


def _pool(client: httpx.AsyncClient):
    """Walk down to the connection pool the requests actually go through."""
    transport = client._transport
    assert isinstance(transport, ModelStreamBudgetTransport)
    return transport.wrapped.wrapped._pool  # tenacity -> AsyncHTTPTransport


def test_the_configured_pool_ceiling_reaches_the_pool(client) -> None:
    """The setting was inert: read it off the live pool, not off the config."""
    assert _pool(client)._max_connections == (
        agent_settings.agent_model_http_max_connections
    )


def test_the_configured_keepalive_reaches_the_pool(client) -> None:
    """Keepalive was httpx's 20/5s, not the 100/30s written beside it.

    Worth its own test rather than an extra assertion above: the ceiling
    happened to equal httpx's default, so it would have passed while wrong.
    Keepalive is where the two genuinely differ, and so where the bug shows.
    """
    pool = _pool(client)
    assert pool._max_keepalive_connections == (
        agent_settings.agent_model_http_max_connections
    )
    assert pool._keepalive_expiry == 30.0


def test_the_stream_budget_is_installed_outside_the_retry_layer(client) -> None:
    """Order is load-bearing, not cosmetic.

    Tenacity retries ``handle_async_request``, which is finished once the
    headers arrive — nothing inside it is still watching while the body
    streams. A budget installed underneath it would never see the trickle it
    exists to catch.
    """
    transport = client._transport
    assert isinstance(transport, ModelStreamBudgetTransport)
    assert transport._total_seconds == (
        agent_settings.agent_model_stream_total_timeout_seconds
    )
    assert transport._first_chunk_seconds == (
        agent_settings.agent_model_stream_first_chunk_timeout_seconds
    )


def test_the_per_chunk_read_timeout_still_applies(client) -> None:
    """The budget adds a bound; it does not replace the one already there.

    A custom transport does *not* discard timeouts the way it discards limits —
    they travel per-request in the extensions — so this stays true, and it is
    worth pinning next to the limits bug so the two are not conflated later.
    """
    assert client.timeout.read == agent_settings.agent_model_http_read_timeout_seconds
    assert client.timeout.connect == (
        agent_settings.agent_model_http_connect_timeout_seconds
    )
