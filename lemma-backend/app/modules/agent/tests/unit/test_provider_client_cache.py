"""The pooled provider clients: bounded, and holding nothing secret.

Nothing evicted from the cache, and each entry pins an ``httpx2.AsyncClient``
with its own connection pool, so on a multi-tenant deployment its size grew
with the customer list for the life of the process.

The pool used to be keyed by credential, because the profile's headers were
baked into the client and had to be kept apart per tenant. Keying by credential
meant holding the key -- or something derived from it -- as a dict key. Now
the client carries no credential and no header at all: the SDK client built
over it sends those per request. So the key names only the endpoint and the
runtime profile, and nothing in it is secret.
"""

from __future__ import annotations

import httpx2
import pytest

from app.modules.agent.infrastructure.providers import model_factory as factory


@pytest.fixture(autouse=True)
def _clean_cache():
    factory._provider_clients.clear()
    yield
    factory._provider_clients.clear()


_PROFILE = {
    "profile_id": "profile-a",
    "protocol": "OPENAI_COMPATIBLE",
    "provider_model_name": "model-a",
    "config": {
        "base_url": "https://api.example.com/v1",
        "headers": {"X-Gateway": "header-secret"},
    },
}


def _model(profile: dict[str, object], api_key: str):
    return factory._unmetered_model_from_runtime_profile(
        runtime_profile=profile, runtime_credentials={"api_key": api_key}
    )


def test_no_pooled_client_or_cache_key_holds_a_key_or_header_value() -> None:
    """A dict key outlives every SecretStr that held it; keep secrets out."""
    _model(_PROFILE, "sk-plaintext-key")

    (key,) = list(factory._provider_clients)
    (client,) = list(factory._provider_clients.values())
    held = key + repr(dict(client.headers))
    assert "sk-plaintext-key" not in held
    assert "header-secret" not in held


def test_the_sdk_client_sends_the_profile_s_key_and_headers() -> None:
    """What left the pooled client still reaches the provider, per request."""
    model = _model(_PROFILE, "sk-plaintext-key")

    sdk = model.client
    assert sdk.api_key == "sk-plaintext-key"
    assert sdk.default_headers["X-Gateway"] == "header-secret"


def test_two_profiles_on_one_endpoint_keep_their_own_pools() -> None:
    _model(_PROFILE, "sk-a")
    _model({**_PROFILE, "profile_id": "profile-b"}, "sk-b")

    assert len(factory._provider_clients) == 2


def test_a_rotated_key_reuses_the_profile_s_pool() -> None:
    """The pool holds no credential, so a new key needs no new pool."""
    first = _model(_PROFILE, "sk-old")
    second = _model(_PROFILE, "sk-new")

    assert len(factory._provider_clients) == 1
    assert second.client.api_key == "sk-new"
    assert first.client.api_key == "sk-old"


async def test_the_pool_keeps_no_cookies_between_runs() -> None:
    """A cookie set for one run would otherwise ride on the next one's request."""

    def handler(request: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(200, headers={"Set-Cookie": "session=tenant-a"})

    client = httpx2.AsyncClient(
        cookies=factory._no_cookies(), transport=httpx2.MockTransport(handler)
    )
    async with client:
        await client.get("https://api.example.com/v1/models")
        assert not client.cookies


def test_the_client_cache_stays_bounded_and_reuses_live_entries() -> None:
    limit = factory._MAX_PROVIDER_CLIENTS

    first = factory.get_provider_http_client(
        protocol="openai_compat", base_url="https://a.test", profile_id="p"
    )
    again = factory.get_provider_http_client(
        protocol="openai_compat", base_url="https://a.test", profile_id="p"
    )
    assert first is again, "a warm entry must be reused, not rebuilt"

    for i in range(limit + 10):
        factory.get_provider_http_client(
            protocol="openai_compat", base_url=f"https://host-{i}.test", profile_id="p"
        )

    assert len(factory._provider_clients) <= limit
