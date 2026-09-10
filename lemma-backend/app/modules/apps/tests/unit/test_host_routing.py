from starlette.datastructures import Headers
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
    # The app-origin API door is gated; these tests are about what it does when
    # a deployment has opted in.
    monkeypatch.setattr(settings, "app_api_via_app_origin", True)


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
    assert app_slug_from_host(host) == (expected, None)


def test_no_base_domain_disables_routing(monkeypatch):
    monkeypatch.setattr(settings, "app_base_domain", "")
    assert app_slug_from_host("my-app.apps.lemma.localhost:8711") == (None, None)


async def _drive(
    path, *, host=b"my-app.apps.lemma.localhost:8711", proxied=False, extra_headers=None
):
    """Run the middleware for a request and return the downstream scope.

    ``proxied=True`` reproduces the cloud path, where the nginx ingress has
    already resolved the slug and set the header itself.
    """
    seen = {}

    async def downstream(scope, receive, send):
        seen["scope"] = scope

    middleware = AppHostRoutingMiddleware(downstream)
    headers = [(b"host", host), *(extra_headers or [])]
    if proxied:
        headers.append((b"x-app-public-slug", b"my-app"))
    scope = {
        "type": "http",
        "path": path,
        # utf-8, as uvicorn builds it -- `scope["path"]` is a decoded str and
        # `raw_path` its bytes.
        "raw_path": path.encode("utf-8"),
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
async def test_the_prefix_means_the_same_thing_behind_the_ingress():
    # The path an ingress actually delivers, not the one it would be convenient
    # to assert. `nginx.conf` proxies app hosts with
    # `proxy_pass .../public/apps$request_uri`, and a proxy_pass whose URI
    # contains a variable is sent verbatim -- so the backend sees the prefix
    # already nested under the asset endpoint.
    #
    # The first version of this test hand-built `/_lemma/users/me` for the
    # proxied case, which nginx never produces. It passed while every API call
    # behind that ingress fell through to the asset controller and came back
    # 200 with the app's own index.html.
    nested = await _drive("/public/apps/_lemma/users/me", proxied=True)
    assert nested["path"] == "/users/me"

    # An ingress that rewrites nothing and only sets the header works too.
    bare = await _drive("/_lemma/users/me", proxied=True)
    assert bare["path"] == "/users/me"

    # ...while ordinary proxied asset requests stay exactly as they were.
    asset = await _drive("/assets/app.js", proxied=True)
    assert asset["path"] == "/assets/app.js"
    nested_asset = await _drive("/public/apps/assets/app.js", proxied=True)
    assert nested_asset["path"] == "/public/apps/assets/app.js"


@pytest.mark.asyncio
async def test_the_door_is_shut_unless_the_deployment_opened_it(monkeypatch):
    # The prefix is only handed to apps where the setting is on; the strip has
    # to be gated on the same thing. Ungated, any deployment whose app domain
    # resolves straight to the backend gained a same-origin alias of the entire
    # API on the origin that serves user-authored HTML, without opting in.
    monkeypatch.setattr(settings, "app_api_via_app_origin", False)
    scope = await _drive("/_lemma/users/me")
    assert scope["path"] == "/public/apps/_lemma/users/me"
    assert (b"x-app-public-slug", b"my-app") in scope["headers"]


@pytest.mark.asyncio
async def test_an_asset_name_outside_latin_1_does_not_explode():
    # `raw_path` is bytes and uvicorn hands the middleware a decoded str, so
    # encoding it as latin-1 raised UnicodeEncodeError -- a bare 500 with a
    # traceback for any app shipping an emoji- or CJK-named file.
    scope = await _drive("/assets/\u56fe\u6807.png")
    assert scope["path"] == "/public/apps/assets/\u56fe\u6807.png"
    assert scope["raw_path"] == scope["path"].encode("utf-8")


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
    scope = await _drive(path, host=host, extra_headers=[(b"x-app-release", b"r1")])
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
