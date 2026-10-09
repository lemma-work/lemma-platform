"""Host-based routing for public app builds.

An app is served at ``<public_slug>.<app_base_domain>`` (e.g.
``my-app.apps.lemma.localhost:8711`` locally, ``my-app.apps.lemma.work`` in
cloud). This middleware inspects the request ``Host`` header and, when it
matches an app subdomain, rewrites the request onto the public app asset
endpoint (``/public/apps``) and surfaces the app's label via the
``X-App-Public-Slug`` header.

The Host header is the only source of the label. Every supported ingress -- the
cloud gateway, the self-hosted Caddy config, the desktop -- passes Host through
untouched and rewrites nothing, so an ``X-App-Public-Slug`` arriving from
outside can only have come from the client. It is dropped on every host.
Honouring it let a request on an app host skip the rewrite and reach ordinary
API routes on the origin that renders user-authored HTML, and let any host
serve any app's build by naming it.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings
from app.core.runtime_config import APP_ORIGIN_API_URL

_SLUG_HEADER = b"x-app-public-slug"
_RELEASE_HEADER = b"x-app-release"
_APP_PATH_PREFIX = "/public/apps"

# Real global backend routes that must stay reachable even on an app host — the
# browser SDK an app loads, signed datastore files (e.g. images), and icons. A
# app's own assets are served at the subdomain root (``/``, ``/assets/...``),
# never under these prefixes, so passing them through is safe. (Widgets are
# loaded from the API host, not an app subdomain, so they need no passthrough.)
_GLOBAL_PUBLIC_PREFIXES = (
    "/public/sdk/",
    "/public/datastore/",
    "/public/icons/",
    # Short file links. Same reason as /public/datastore/ above — this is the
    # other half of the same feature. Without it a short link fetched on an app
    # host is rewritten to `/public/apps/...` and 404s as a missing asset of
    # that app.
    "/public/s/",
    # The links' old path, until its date in `LEGACY_ALIASES`
    # (app/core/auth_exemptions.py); goes with that alias.
    "/s/",
)

# The app's own door onto the API, served from the app's own origin.
#
# An app used to call the API at its real host (``app.lemma.localhost`` on
# desktop). That is a different *site* to a browser -- and on desktop it is a
# different site even to the parts of the URL that look shared, because
# `localhost` is not in the Public Suffix List, so WebKit cannot derive a
# registrable domain and treats every `.localhost` host as its own site. Those
# calls were third-party, ITP dropped the session cookie, and every pod app
# loaded signed out. Cookie attributes cannot fix that; only being first-party
# can.
#
# So the SDK is pointed at ``<app-origin>/_lemma`` (see
# ``app.core.runtime_config.build_runtime_config``) and this prefix is stripped
# back off here. Same origin as the page, so the cookie is first-party and the
# browser sends it, and no CORS preflight is involved at all.
#
# A reserved prefix rather than "pass anything that matches a backend route":
# an app owns every other path on its origin, and `/users` is a plausible thing
# for one to ship.
_APP_API_PREFIX = APP_ORIGIN_API_URL
_APP_ACCESS_REDEEM = "/app-access/redeem"


def split_release_label(label: str) -> tuple[str | None, str | None]:
    """Split an app host label into ``(slug, release_ref)``.

    ``orders`` serves whatever is live; ``orders--r7`` previews release 7 of
    ``orders``. ``--`` is unambiguous as the separator because
    ``normalize_public_slug`` collapses runs of ``-``, so a stored slug never
    contains one -- splitting on the LAST ``--`` recovers the slug exactly,
    however many single hyphens it has.

    Shared by the host middleware and the public controller on purpose: the
    middleware hands the controller the WHOLE label, and one function means the
    two cannot disagree about where a label divides.
    """
    if not label or "--" not in label:
        return (label or None), None
    slug, _, release_ref = label.rpartition("--")
    # A label that is all separator ("--r7", "orders--") names no app or no
    # release; treat it as unroutable rather than guessing which half was meant.
    if not slug or not release_ref:
        return None, None
    return slug, release_ref


def app_label_from_host(host: str) -> str | None:
    """Return the routable app label in ``host`` (``orders`` or ``orders--r7``).

    ``host`` may include a port. The label is the single left-most one in front
    of the configured ``app_base_domain``; the bare base domain (the main API
    host) and multi-level hosts are not apps. A label that names no app or no
    release (``--r7``, ``orders--``) is unroutable rather than a guess.
    """
    base = settings.app_base_domain
    if not base:
        return None
    host_no_port = host.split(":", 1)[0].strip().lower()
    base_no_port = base.split(":", 1)[0].strip().lower()
    if not host_no_port or not base_no_port:
        return None
    suffix = f".{base_no_port}"
    if not host_no_port.endswith(suffix):
        return None
    label = host_no_port[: -len(suffix)]
    if not label or "." in label:
        return None
    slug, _release = split_release_label(label)
    return label if slug else None


def app_slug_from_host(host: str) -> tuple[str | None, str | None]:
    """``(slug, release_ref)`` for ``host``, or ``(None, None)``.

    The middleware routes on the whole label; this is the split form, kept for
    callers that want the two halves separately.
    """
    label = app_label_from_host(host)
    return split_release_label(label) if label else (None, None)


def _strip_app_api_prefix(path: str) -> str | None:
    """Return ``path`` with the app-origin API prefix removed, or None.

    ``/_lemma/users/me`` -> ``/users/me``; bare ``/_lemma`` -> ``/``. Anything
    else -- including ``/_lemmatron`` -- is an ordinary app path and is left
    alone, so the prefix cannot swallow a route that merely starts with the same
    letters.

    """
    # Redeeming app access is the app host's own door, not an API call.
    if path == f"{_APP_API_PREFIX}{_APP_ACCESS_REDEEM}":
        return None
    if path == _APP_API_PREFIX:
        return "/"
    if path.startswith(f"{_APP_API_PREFIX}/"):
        return path[len(_APP_API_PREFIX) :]
    return None


def _trusted_routing_headers(
    incoming: list[tuple[bytes, bytes]],
) -> tuple[str, list[tuple[bytes, bytes]]]:
    """The Host, and the headers minus the two only this middleware may set.

    Both are dropped unconditionally, on every host: nothing upstream sets
    either, so one that arrives came from the client. The slug header named the
    app to serve; the release header pinned the canonical live host to an old
    build. The label in Host carries both now.
    """
    host = ""
    headers: list[tuple[bytes, bytes]] = []
    for key, value in incoming:
        lowered = key.lower()
        if lowered in (_SLUG_HEADER, _RELEASE_HEADER):
            continue
        if lowered == b"host":
            host = value.decode("latin-1")
        headers.append((key, value))
    return host, headers


class AppHostRoutingMiddleware:
    """Serve app builds via host-based routing (see module docstring)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        host, headers = _trusted_routing_headers(scope["headers"])
        scope["headers"] = headers
        path = scope.get("path") or "/"
        label = app_label_from_host(host)
        if label is None:
            await self.app(scope, receive, send)
            return

        # The app calling the API on its own origin. Gated on the same setting
        # that hands apps the prefix in the first place. Ungated, any deployment
        # whose app domain resolves straight to the backend got a same-origin
        # alias of the whole API on the origin that renders user-authored HTML
        # -- without ever opting in, and without the refresh-cookie half that
        # makes it actually work.
        if settings.app_api_via_app_origin:
            api_path = _strip_app_api_prefix(path)
            if api_path is not None:
                # No slug header added: this is an API call, not an app asset.
                scope["path"] = api_path
                # utf-8, not latin-1: uvicorn hands us a decoded str, so an app
                # shipping `图标.png` or an emoji-named asset raised
                # UnicodeEncodeError here and 500ed with a traceback.
                scope["raw_path"] = api_path.encode("utf-8")
                await self.app(scope, receive, send)
                return

        # Real global /public routes (SDK, datastore, icons, widgets) are not app
        # assets — let them reach their own handlers instead of 404ing as a
        # missing asset of this app.
        if path.startswith(_GLOBAL_PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        # Every other path is the app's own -- `/public/apps/...` included, which
        # an app may ship -- so it is always rewritten under the asset endpoint.
        new_path = _APP_PATH_PREFIX if path == "/" else _APP_PATH_PREFIX + path

        # The whole label, not the slug: the controller splits it, so a preview
        # host (`orders--r7`) needs nothing of its own.
        new_headers = [*headers, (_SLUG_HEADER, label.encode("latin-1"))]

        # Mutated in place rather than copied. Starlette's router records the
        # matched route by writing `scope["route"]`, and the request observer
        # that reads it sits *outside* this middleware — so with a copy the
        # router wrote to an object the observer never saw, and every app-host
        # request was logged as `route: "unmatched"`. That covered the whole
        # apps product: every slow request it served landed in a bucket no
        # per-route dashboard could attribute to anything.
        scope["path"] = new_path
        scope["raw_path"] = new_path.encode("utf-8")
        scope["headers"] = new_headers
        await self.app(scope, receive, send)
