import pytest

from app.core.config import settings
from app.modules.apps.api.host_routing import (
    AppHostRoutingMiddleware,
    app_slug_from_host,
)

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _base_domain(monkeypatch):
    monkeypatch.setattr(settings, "app_base_domain", "apps.lemma.localhost:8711")


@pytest.mark.parametrize(
    "host,expected",
    [
        ("my-app.apps.lemma.localhost:8711", "my-app"),
        ("my-app.apps.lemma.localhost", "my-app"),  # port optional
        ("MY-APP.APPS.LEMMA.LOCALHOST:8711", "my-app"),  # case-insensitive
        ("apps.lemma.localhost:8711", None),  # bare base domain is not an app
        ("apps.lemma.localhost", None),
        ("a.b.apps.lemma.localhost:8711", None),  # multi-level is not an app
        ("example.com", None),  # unrelated host
        ("", None),
    ],
)
def test_app_slug_from_host(host, expected):
    assert app_slug_from_host(host) == expected


def test_no_base_domain_disables_routing(monkeypatch):
    monkeypatch.setattr(settings, "app_base_domain", "")
    assert app_slug_from_host("my-app.apps.lemma.localhost:8711") is None


async def _drive(path):
    """Run the middleware for an app-host request and return the downstream scope."""
    seen = {}

    async def downstream(scope, receive, send):
        seen["scope"] = scope

    middleware = AppHostRoutingMiddleware(downstream)
    scope = {
        "type": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "headers": [(b"host", b"my-app.apps.lemma.localhost:8711")],
    }

    async def receive():
        return {"type": "http.request"}

    async def send(_message):
        return None

    await middleware(scope, receive, send)
    return seen["scope"]


@pytest.mark.asyncio
async def test_global_public_routes_pass_through_on_app_host():
    # The browser SDK (and other real /public routes) must reach their own
    # handler, not be rewritten into a missing app asset.
    scope = await _drive("/public/sdk/lemma-client.js")
    assert scope["path"] == "/public/sdk/lemma-client.js"
    assert all(key != b"x-app-public-slug" for key, _ in scope["headers"])


@pytest.mark.asyncio
async def test_app_assets_are_rewritten_with_slug():
    scope = await _drive("/assets/app.js")
    assert scope["path"] == "/public/apps/assets/app.js"
    assert (b"x-app-public-slug", b"my-app") in scope["headers"]

    root = await _drive("/")
    assert root["path"] == "/public/apps"
