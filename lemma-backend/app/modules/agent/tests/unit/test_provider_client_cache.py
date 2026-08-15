"""The provider-client cache is keyed by tenant credential, so it must be bounded.

Nothing evicted from it, and each entry pins an ``httpx.AsyncClient`` with its own
connection pool, so on a multi-tenant deployment its size grew with the customer
list for the life of the process.

Bounding it introduced a sharper problem than the leak it fixed. Credential
labels were numbered ``credential-{len(map) + 1}``; once the map can shrink, its
length repeats, and a repeated length hands an existing label to a *different*
credential. Two tenants whose labels collide share a cache key, and therefore a
client, and therefore an Authorization header. The counter must never rewind.
"""

from __future__ import annotations

import pytest

from app.modules.agent.services import runtime_model_factory as factory


@pytest.fixture(autouse=True)
def _clean_cache():
    factory._provider_clients.clear()
    factory._credential_labels.clear()
    yield
    factory._provider_clients.clear()
    factory._credential_labels.clear()


def test_the_same_credential_keeps_one_label() -> None:
    assert factory._credential_label("sk-a") == factory._credential_label("sk-a")


def test_different_credentials_never_share_a_label() -> None:
    assert factory._credential_label("sk-a") != factory._credential_label("sk-b")


def test_a_label_is_never_reissued_after_eviction() -> None:
    """The bug a length-derived counter would reintroduce.

    Fill past the cap so the earliest labels are evicted, then issue more. If any
    new credential were handed a label an older one still holds, two tenants
    would resolve to the same client.
    """
    limit = factory._MAX_PROVIDER_CLIENTS
    issued = [factory._credential_label(f"sk-{i}") for i in range(limit * 2)]

    assert len(set(issued)) == len(issued), "a label was reissued to a second key"


def test_the_label_map_stays_bounded() -> None:
    limit = factory._MAX_PROVIDER_CLIENTS
    for i in range(limit * 3):
        factory._credential_label(f"sk-{i}")

    assert len(factory._credential_labels) <= limit


def test_an_absent_credential_is_not_numbered() -> None:
    """No key is one identity, not a fresh one per call."""
    assert factory._credential_label(None) == "anonymous"
    assert factory._credential_label("") == "anonymous"
    assert not factory._credential_labels


def test_the_client_cache_stays_bounded_and_reuses_live_entries() -> None:
    limit = factory._MAX_PROVIDER_CLIENTS

    first = factory.get_provider_http_client(
        protocol="openai_compat", base_url="https://a.test", api_key="k", headers={}
    )
    again = factory.get_provider_http_client(
        protocol="openai_compat", base_url="https://a.test", api_key="k", headers={}
    )
    assert first is again, "a warm entry must be reused, not rebuilt"

    for i in range(limit + 10):
        factory.get_provider_http_client(
            protocol="openai_compat",
            base_url=f"https://host-{i}.test",
            api_key="k",
            headers={},
        )

    assert len(factory._provider_clients) <= limit
