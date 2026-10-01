"""The token and revocation limits: per client, with a ceiling per address.

The caller names the client, so a per-client bucket alone would give anyone who
invents a new ``client_id`` for every request a fresh budget every time.
"""

from __future__ import annotations

from collections import Counter

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response
from starlette.routing import Route
from starlette.testclient import TestClient

from app.modules.mcp_access.api.oauth_routes import _by_client_and_address, _limited
from app.modules.mcp_access.infrastructure.rate_limit import RateLimiter


class _CountingLimiter(RateLimiter):
    """Counts in memory, as the Redis script does per key."""

    def __init__(self) -> None:
        super().__init__()
        self.counts: Counter[str] = Counter()

    async def retry_after(
        self, key: str, *, limit: int, window_seconds: int
    ) -> int | None:
        self.counts[key] += 1
        return window_seconds if self.counts[key] > limit else None


async def _ok(_: Request) -> Response:
    return PlainTextResponse("ok")


def _app(limiter: _CountingLimiter, *, per_client: int, per_address: int) -> Starlette:
    endpoint = _limited(
        _ok,
        name="token",
        key=_by_client_and_address,
        limit=lambda: per_client,
        window=60,
        address_limit=lambda: per_address,
        limiter=lambda: limiter,
    )
    return Starlette(routes=[Route("/token", endpoint, methods=["POST"])])


@pytest.fixture
def limiter() -> _CountingLimiter:
    return _CountingLimiter()


def test_one_client_is_held_to_its_own_limit(limiter: _CountingLimiter) -> None:
    client = TestClient(_app(limiter, per_client=2, per_address=100))

    answers = [
        client.post("/token", data={"client_id": "claude"}).status_code
        for _ in range(3)
    ]

    assert answers == [200, 200, 429]


def test_inventing_a_client_per_request_hits_the_address_ceiling(
    limiter: _CountingLimiter,
) -> None:
    client = TestClient(_app(limiter, per_client=2, per_address=3))

    answers = [
        client.post(
            "/token", data={"client_id": f"https://evil.example/c.json?{n}"}
        ).status_code
        for n in range(4)
    ]

    assert answers == [200, 200, 200, 429]


def test_the_ceiling_is_checked_before_the_client_is_looked_at(
    limiter: _CountingLimiter,
) -> None:
    """Refused at the address, the request never reaches the handler -- which
    is where a URL-shaped client id would be fetched."""
    client = TestClient(_app(limiter, per_client=100, per_address=1))
    client.post("/token", data={"client_id": "a"})

    refused = client.post("/token", data={"client_id": "b"})

    assert refused.status_code == 429
    assert refused.headers["Retry-After"] == "60"
    assert not any(key.endswith(":b") for key in limiter.counts)
