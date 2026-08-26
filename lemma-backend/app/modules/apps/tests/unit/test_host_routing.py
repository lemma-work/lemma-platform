import pytest

from app.core.config import settings
from app.core.runtime_config import APP_ORIGIN_API_URL, build_runtime_config
from app.modules.apps.api.host_routing import (
    AppHostRoutingMiddleware,
    _strip_app_api_prefix,
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


async def _drive(path, *, host=b"my-app.apps.lemma.localhost:8711", proxied=False):
    """Run the middleware for a request and return the downstream scope.

    ``proxied=True`` reproduces the cloud path, where the nginx ingress has
    already resolved the slug and set the header itself.
    """
    seen = {}

    async def downstream(scope, receive, send):
        seen["scope"] = scope

    middleware = AppHostRoutingMiddleware(downstream)
    headers = [(b"host", host)]
    if proxied:
        headers.append((b"x-app-public-slug", b"my-app"))
    scope = {
        "type": "http",
        "path": path,
        "raw_path": path.encode("latin-1"),
        "headers": headers,
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


# --- the app's own door onto the API ------------------------------------------
#
# Why this exists at all: a browser only sends the session cookie on a
# first-party request, and on desktop `app.lemma.localhost` and
# `<slug>.apps.lemma.localhost` are separate *sites* (localhost is not a public
# suffix, so WebKit cannot derive a registrable domain). An app calling the API
# at its real host therefore got no cookie and rendered signed out. Routing
# those calls through the app's own origin is what makes them first-party.


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "requested,expected",
    [
        ("/_lemma/users/me", "/users/me"),
        ("/_lemma/st/auth/session/refresh", "/st/auth/session/refresh"),
        ("/_lemma", "/"),
    ],
)
async def test_the_app_reaches_the_api_through_its_own_origin(requested, expected):
    scope = await _drive(requested)
    assert scope["path"] == expected
    assert scope["raw_path"] == expected.encode("latin-1")
    # Not an asset request, so it must not be tagged as one — a slug header
    # here would send it to the public app controller instead of the API.
    assert all(key != b"x-app-public-slug" for key, _ in scope["headers"])


@pytest.mark.asyncio
async def test_the_prefix_does_not_swallow_an_apps_own_paths():
    # An app owns every other path on its origin. Matching on the bare string
    # would capture `/_lemmatron` and any asset whose name merely starts the
    # same way, turning one of the app's own files into a 404 from the API.
    for path in ("/_lemmatron", "/_lemma-notes/index.html"):
        scope = await _drive(path)
        assert scope["path"] == f"/public/apps{path}"
        assert (b"x-app-public-slug", b"my-app") in scope["headers"]


@pytest.mark.asyncio
async def test_the_prefix_means_the_same_thing_behind_the_cloud_ingress():
    # nginx resolves the slug upstream, and everything else on that path is
    # deliberately left untouched. The prefix still has to be understood, or
    # the same app build works on desktop and 404s in cloud.
    scope = await _drive("/_lemma/users/me", proxied=True)
    assert scope["path"] == "/users/me"

    # ...while ordinary proxied asset requests stay exactly as they were.
    asset = await _drive("/assets/app.js", proxied=True)
    assert asset["path"] == "/assets/app.js"


@pytest.mark.asyncio
async def test_the_prefix_is_inert_off_an_app_host():
    # Only an app origin has a reason to carry it. On the API host it is just
    # an unknown path, and stripping it there would quietly expose every route
    # under a second spelling.
    scope = await _drive("/_lemma/users/me", host=b"app.lemma.localhost:8711")
    assert scope["path"] == "/_lemma/users/me"


def test_the_served_config_points_at_a_prefix_the_middleware_strips():
    # The two halves live in different modules and only work as a pair: the
    # config tells the app where to call, this middleware takes it back off.
    # Driven through build_runtime_config rather than compared to the constant,
    # so a change to what apps are actually handed cannot stop matching quietly.
    api_url = build_runtime_config(
        "00000000-0000-0000-0000-000000000000",
        api_url=APP_ORIGIN_API_URL,
    )["apiUrl"]
    assert _strip_app_api_prefix(f"{api_url}/users/me") == "/users/me"

    # Relative, so the SDK resolves it against the app's own origin. An
    # absolute URL here would point back at the API host and be cross-site
    # again -- the exact shape that made every pod app load signed out.
    assert not api_url.startswith("http")
    assert api_url.startswith("/")
