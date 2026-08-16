import pytest
from starlette.datastructures import Headers

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
        ("my-app.apps.lemma.localhost:8711", ("my-app", None)),
        ("my-app.apps.lemma.localhost", ("my-app", None)),  # port optional
        ("MY-APP.APPS.LEMMA.LOCALHOST:8711", ("my-app", None)),  # case-insensitive
        # Bare base domain is not an app; nor is a multi-level host.
        ("apps.lemma.localhost:8711", (None, None)),
        ("apps.lemma.localhost", (None, None)),
        ("a.b.apps.lemma.localhost:8711", (None, None)),
        ("example.com", (None, None)),  # unrelated host
        ("", (None, None)),
    ],
)
def test_app_slug_from_host(host, expected):
    assert app_slug_from_host(host) == expected


@pytest.mark.parametrize(
    "host,expected",
    [
        # A preview host carries the release in the same label.
        ("my-app--r7.apps.lemma.localhost:8711", ("my-app", "r7")),
        ("my-app--r7.apps.lemma.localhost", ("my-app", "r7")),
        # A slug's own hyphens survive: the split is on the LAST `--`, and
        # normalize_public_slug collapses runs of `-` so a stored slug can never
        # contain one itself.
        ("a-long-app-name--r12.apps.lemma.localhost", ("a-long-app-name", "r12")),
        # Addressing a release by digest prefix works the same way.
        ("my-app--9f8e7d.apps.lemma.localhost", ("my-app", "9f8e7d")),
        # Half a label names no app or no release -- refuse rather than guess.
        ("--r7.apps.lemma.localhost", (None, None)),
        ("my-app--.apps.lemma.localhost", (None, None)),
    ],
)
def test_preview_host_carries_the_release(host, expected):
    assert app_slug_from_host(host) == expected


def test_no_base_domain_disables_routing(monkeypatch):
    monkeypatch.setattr(settings, "app_base_domain", "")
    assert app_slug_from_host("my-app.apps.lemma.localhost:8711") == (None, None)


async def _drive(path, host=b"my-app.apps.lemma.localhost:8711", extra_headers=None):
    """Run the middleware for an app-host request and return the downstream scope.

    ``extra_headers`` are inbound headers a client (or a proxy) sent, which is
    the whole point for the spoofing tests: the fixture used to send only Host,
    so nothing exercised what happens to a header the middleware also sets.
    """
    seen = {}

    async def downstream(scope, receive, send):
        seen["scope"] = scope

    middleware = AppHostRoutingMiddleware(downstream)
    scope = {
        "type": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "headers": [(b"host", host), *(extra_headers or [])],
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
    # A live host names no release, so nothing pins the serving path off current.
    assert all(key != b"x-app-release" for key, _ in scope["headers"])

    root = await _drive("/")
    assert root["path"] == "/public/apps"


@pytest.mark.asyncio
async def test_preview_host_pins_the_release_on_every_asset():
    # The release rides the host, not the path, precisely so that a Vite build's
    # absolute `/assets/...` request stays on the previewed release instead of
    # falling back to whatever is live. It travels as part of the slug label --
    # one mechanism, the same one the cloud ingress already forwards -- so there
    # is no second header for a client to forge.
    scope = await _drive("/assets/app.js", host=b"my-app--r7.apps.lemma.localhost:8711")
    assert scope["path"] == "/public/apps/assets/app.js"
    assert (b"x-app-public-slug", b"my-app--r7") in scope["headers"]
    assert all(key != b"x-app-release" for key, _ in scope["headers"])


@pytest.mark.parametrize(
    "host,path",
    [
        (b"my-app.apps.lemma.localhost:8711", "/assets/app.js"),
        (b"my-app--r7.apps.lemma.localhost:8711", "/assets/app.js"),
        (b"my-app.apps.lemma.localhost:8711", "/public/sdk/lemma-client.js"),
        (b"api.lemma.localhost:8711", "/public/apps/assets/app.js"),
    ],
)
@pytest.mark.asyncio
async def test_a_client_supplied_release_header_never_survives(host, path):
    """The security regression.

    Nothing upstream sets this header -- neither nginx config does -- so one
    arriving from a client was honoured verbatim, and anyone could serve any
    superseded build from the canonical live host by adding a line to a request.
    """
    scope = await _drive(
        path, host=host, extra_headers=[(b"x-app-release", b"r1")]
    )
    assert all(key != b"x-app-release" for key, _ in scope["headers"])


@pytest.mark.asyncio
async def test_a_client_supplied_slug_cannot_beat_the_host():
    """Starlette resolves a repeated header to the FIRST occurrence, so the
    middleware has to replace rather than append."""
    scope = await _drive(
        "/assets/app.js",
        host=b"my-app.apps.lemma.localhost:8711",
        extra_headers=[(b"x-app-public-slug", b"other-app--r1")],
    )
    slugs = [value for key, value in scope["headers"] if key == b"x-app-public-slug"]
    assert slugs == [b"my-app"]
    assert Headers(raw=scope["headers"]).get("x-app-public-slug") == "my-app"


@pytest.mark.asyncio
async def test_a_proxied_request_is_not_rewritten_twice():
    """In cloud the ingress resolves the label AND rewrites the path, forwarding
    the original Host. Rewriting again would ask for
    /public/apps/public/apps/assets/app.js and 404 every app in production."""
    scope = await _drive(
        "/public/apps/assets/app.js",
        host=b"my-app--r7.apps.lemma.localhost:8711",
        extra_headers=[(b"x-app-public-slug", b"my-app--r7")],
    )
    assert scope["path"] == "/public/apps/assets/app.js"
    assert (b"x-app-public-slug", b"my-app--r7") in scope["headers"]


@pytest.mark.asyncio
async def test_a_non_app_host_keeps_the_proxy_supplied_slug():
    """This branch IS the cloud ingress contract. Stripping the slug here would
    take every app down to close a hole that only re-exposes bytes already
    public at their own preview host."""
    scope = await _drive(
        "/public/apps/assets/app.js",
        host=b"api.lemma.localhost:8711",
        extra_headers=[(b"x-app-public-slug", b"my-app--r7")],
    )
    assert scope["path"] == "/public/apps/assets/app.js"
    assert (b"x-app-public-slug", b"my-app--r7") in scope["headers"]
