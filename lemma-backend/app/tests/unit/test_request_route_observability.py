"""A request must be logged under the route it actually matched.

This has now gone wrong twice, the same way both times. Middleware that
rewrites the path by *copying* the ASGI scope breaks route attribution,
because the router records its match by writing ``scope["route"]`` and
:class:`RequestObserverMiddleware` reads it from further out — so the router
writes to an object the observer never sees, and the request is logged as
``route: "unmatched"``.

The first occurrence covered the whole apps product: 52 slow 404s and 21 slow
200s in a day, none of them attributable to any route. The second was
``TrailingSlashMiddleware``, which put every request whose path ends in a slash
into the same bucket — the bucket in which production's fixed-cost 5.2s 404s
were then investigated, twice, without success.

So this pins the property rather than either middleware: with the real
middleware stack in the real order, a request that matches a route is logged
under that route's template.
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from app.app import RequestObserverMiddleware, TrailingSlashMiddleware

pytestmark = pytest.mark.unit


def _observed_routes(monkeypatch) -> list[str]:
    """Capture the route label the observer resolves for each request."""
    seen: list[str] = []
    original = RequestObserverMiddleware._route_template

    def _record(scope):
        label = original(scope)
        seen.append(label)
        return label

    monkeypatch.setattr(
        RequestObserverMiddleware, "_route_template", staticmethod(_record)
    )
    return seen


def _client() -> TestClient:
    app = FastAPI()

    @app.get("/things/{thing_id}")
    async def _thing(thing_id: str):
        return {"thing_id": thing_id}

    # The real order: the observer is added last so it is outermost, and the
    # slash rewriter is added first so it sits closest to the router.
    app.add_middleware(TrailingSlashMiddleware)
    app.add_middleware(RequestObserverMiddleware)
    return TestClient(app)


def test_a_matched_route_is_observed_under_its_template(monkeypatch):
    seen = _observed_routes(monkeypatch)

    with _client() as client:
        assert client.get("/things/abc").status_code == 200

    assert seen == ["/things/{thing_id}"]


def test_a_trailing_slash_does_not_cost_the_request_its_route(monkeypatch):
    """The regression. This asked for the same handler and got the same 200,
    but used to be filed under a label that names no request at all."""
    seen = _observed_routes(monkeypatch)

    with _client() as client:
        assert client.get("/things/abc/").status_code == 200

    assert seen == ["/things/{thing_id}"], (
        "a stray trailing slash put the request in the unmatched bucket"
    )


def test_a_request_that_matches_nothing_is_still_unmatched(monkeypatch):
    """The label is not wrong, only uninformative — it must stay for real 404s."""
    seen = _observed_routes(monkeypatch)

    with _client() as client:
        assert client.get("/nothing/here").status_code == 404

    assert seen == ["unmatched"]
