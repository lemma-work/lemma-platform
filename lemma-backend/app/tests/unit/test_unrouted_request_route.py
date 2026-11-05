"""A request refused before routing still names the route it was headed for."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.trace import SpanKind

from app.middleware import RequestObserverMiddleware

pytestmark = pytest.mark.unit


def _app() -> FastAPI:
    app = FastAPI()

    @app.get("/pods/{pod_id}/agents")
    async def agents(pod_id: str) -> dict[str, str]:
        return {"pod": pod_id}

    return app


async def _refused_before_routing(path: str, method: str = "GET") -> object:
    """Send ``path`` through the observer to an app that answers 401 itself."""

    async def refuse(scope, receive, send):
        await send({"type": "http.response.start", "status": 401, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def send(message):
        return None

    span = (
        TracerProvider().get_tracer("test").start_span("server", kind=SpanKind.SERVER)
    )
    with trace.use_span(span, end_on_exit=True):
        await RequestObserverMiddleware(refuse)(
            {
                "type": "http",
                "method": method,
                "path": path,
                "headers": [],
                "app": _app(),
            },
            receive,
            send,
        )
    return span


@pytest.mark.asyncio
async def test_a_401_before_routing_carries_the_template_it_would_have_matched():
    span = await _refused_before_routing("/pods/019f99c1/agents")
    assert span.attributes["http.route"] == "/pods/{pod_id}/agents"


@pytest.mark.asyncio
async def test_a_wrong_method_still_names_the_path_template():
    span = await _refused_before_routing("/pods/p/agents", method="DELETE")
    assert span.attributes["http.route"] == "/pods/{pod_id}/agents"


@pytest.mark.asyncio
async def test_a_path_no_route_matches_is_unmatched():
    span = await _refused_before_routing("/nowhere/at/all")
    assert span.attributes["http.route"] == "unmatched"


def _scope(app: object, path: str) -> dict[str, object]:
    return {"type": "http", "method": "GET", "path": path, "headers": [], "app": app}


def test_a_mount_never_names_a_request() -> None:
    from starlette.applications import Starlette
    from starlette.routing import Mount

    from app.middleware import _template_for_unrouted

    app = _app()
    app.router.routes.append(Mount("/st", app=Starlette()))
    assert _template_for_unrouted(_scope(app, "/st/anything")) == "unmatched"


def test_only_routes_sharing_the_first_segment_are_tried() -> None:
    from app.middleware import _template_for_unrouted

    app = _app()
    tried: list[str] = []
    for route in app.router.routes:
        original = route.matches

        def recording(scope, *, _route=route, _original=original):
            tried.append(_route.path_format)
            return _original(scope)

        route.matches = recording  # type: ignore[method-assign]

    assert _template_for_unrouted(_scope(app, "/pods/p/agents")) == (
        "/pods/{pod_id}/agents"
    )
    assert tried == ["/pods/{pod_id}/agents"]


def test_a_parameter_route_registered_first_wins_as_the_router_would() -> None:
    from app.middleware import _template_for_unrouted

    app = FastAPI()

    @app.get("/{account}")
    async def account(account: str) -> None:
        return None

    @app.get("/health")
    async def health() -> None:
        return None

    assert _template_for_unrouted(_scope(app, "/health")) == "/{account}"
