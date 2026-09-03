"""Who owns the connection pool, and what ``close()`` actually closes.

Every derived handle -- ``lemma.pod(...)``, ``lemma.for_org(...)`` -- shares the
parent's transport. A handle that built its own httpx client instead would be
invisible to the parent's ``close()``, so a ``with Lemma.from_env() as lemma:``
block that derived one per organization would leak a connection pool per
iteration with nothing but GC to reclaim it.
"""

from __future__ import annotations

from lemma_sdk import Lemma

ORG_A = "11111111-1111-4111-8111-111111111111"
ORG_B = "33333333-3333-4333-8333-333333333333"
POD = "22222222-2222-4222-8222-222222222222"


def _client() -> Lemma:
    return Lemma(
        token="token",
        base_url="https://api.example.test",
        org_id=ORG_A,
        pod_id=POD,
    )


def test_for_org_shares_the_parent_transport() -> None:
    with _client() as lemma:
        scoped = lemma.for_org(ORG_B)

        assert scoped._transport is lemma._transport
        assert scoped.org_id == ORG_B
        # The rest of the connection is the parent's, not a fresh resolution:
        # a derived client must never dial somewhere else.
        assert scoped.settings.base_url == lemma.settings.base_url
        assert scoped.settings.token == lemma.settings.token
        assert scoped.settings.timeout == lemma.settings.timeout
        assert scoped.settings.verify_ssl == lemma.settings.verify_ssl
        assert scoped.default_pod_id == POD


def test_closing_a_derived_client_leaves_the_parent_usable() -> None:
    lemma = _client()
    scoped = lemma.for_org(ORG_B)

    scoped.close()

    # The parent still owns the pool, so its httpx client is still open.
    assert not lemma._transport.generated.get_httpx_client().is_closed
    lemma.close()
    assert lemma._transport.generated.get_httpx_client().is_closed


def test_closing_the_parent_closes_the_one_pool_every_view_uses() -> None:
    lemma = _client()
    scoped = lemma.for_org(ORG_B)
    pod = lemma.pod(POD)
    # The httpx client is built on first use, and close() is a no-op before
    # that -- so open the pool the way a request would before closing it.
    lemma._transport.generated.get_httpx_client()

    lemma.close()

    assert scoped._transport.generated.get_httpx_client().is_closed
    assert pod._transport.generated.get_httpx_client().is_closed
