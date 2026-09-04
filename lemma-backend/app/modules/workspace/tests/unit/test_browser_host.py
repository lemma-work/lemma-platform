from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.modules.workspace.config import workspace_settings
from app.modules.workspace.services.browser_host import (
    BrowserHostCodeStore,
    browser_code_from_host,
)


class _FakeRedis:
    def __init__(self) -> None:
        self.values: dict[str, str] = {}
        self.ttls: dict[str, int] = {}

    async def set(self, key, value, ex=None):
        self.values[key] = value
        if ex is not None:
            self.ttls[key] = ex

    async def get(self, key):
        return self.values.get(key)


def _store() -> tuple[BrowserHostCodeStore, _FakeRedis]:
    store, redis = BrowserHostCodeStore(), _FakeRedis()
    store._redis = redis
    return store, redis


@pytest.mark.asyncio
async def test_a_code_stands_in_for_a_grant_that_cannot_fit_in_a_label() -> None:
    """A signed grant is ~135 characters; a DNS label stops at 63."""
    store, _ = _store()
    token = "x" * 135

    code = await store.mint(
        token, expires_at=datetime.now(timezone.utc) + timedelta(minutes=10)
    )

    assert len(code) < 63
    assert code.isalnum()
    assert await store.resolve(code) == token


@pytest.mark.asyncio
async def test_the_code_dies_with_the_grant_it_wraps() -> None:
    """Otherwise a stale host outlives the access it was minted for."""
    store, redis = _store()
    expires = datetime.now(timezone.utc) + timedelta(minutes=10)

    code = await store.mint("tok", expires_at=expires)

    assert 0 < redis.ttls[f"workspace:browser-host:v1:{code}"] <= 600


@pytest.mark.asyncio
async def test_an_unknown_code_resolves_to_nothing() -> None:
    store, _ = _store()
    assert await store.resolve("nope") is None


def test_a_browser_host_is_one_label_in_front_of_the_base(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        workspace_settings, "browser_base_domain", "browser.lemma.localhost:8710"
    )
    assert browser_code_from_host("abc123.browser.lemma.localhost:8710") == "abc123"
    assert browser_code_from_host("ABC123.Browser.Lemma.Localhost") == "abc123"


@pytest.mark.parametrize(
    "host",
    [
        # The bare base domain is the API, not a browser.
        "browser.lemma.localhost:8710",
        # Multi-level labels are not a code.
        "a.b.browser.lemma.localhost",
        "api.lemma.localhost",
        "",
    ],
)
def test_what_is_not_a_browser_host_is_left_alone(
    host: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        workspace_settings, "browser_base_domain", "browser.lemma.localhost:8710"
    )
    assert browser_code_from_host(host) is None


def test_without_a_configured_domain_nothing_is_a_browser_host(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An install that has not opted in keeps today's behaviour."""
    monkeypatch.setattr(workspace_settings, "browser_base_domain", None)
    assert browser_code_from_host("abc.browser.lemma.localhost") is None
