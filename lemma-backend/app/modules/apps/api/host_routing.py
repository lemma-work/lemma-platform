"""Host-based routing for public app builds.

An app is served at ``<public_slug>.<app_base_domain>`` (e.g.
``my-app.apps.lemma.localhost:8711`` locally, ``my-app.apps.lemma.work`` in
cloud). This middleware inspects the request ``Host`` header and, when it
matches an app subdomain, rewrites the request onto the public app asset
endpoint (``/public/apps``) and surfaces the slug via the
``X-App-Public-Slug`` header — the same contract the cloud nginx ingress uses.

This lets the backend serve apps by host with no reverse proxy locally, and
keeps a single code path in the controller (slug always arrives as a header).
Requests that already carry ``X-App-Public-Slug`` (i.e. proxied by nginx)
pass through untouched so the cloud path is unchanged.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from app.core.config import settings

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
)


def split_release_label(label: str) -> tuple[str | None, str | None]:
    """Split an app host label into ``(slug, release_ref)``.

    ``orders`` serves whatever is live; ``orders--r7`` previews release 7 of
    ``orders``. ``--`` is unambiguous as the separator because
    ``normalize_public_slug`` collapses runs of ``-``, so a stored slug never
    contains one -- splitting on the LAST ``--`` recovers the slug exactly,
    however many single hyphens it has.

    This is shared by the local host middleware and the public controller on
    purpose. The cloud nginx ingress resolves the slug from the host itself and
    hands the backend the WHOLE label, so the controller has to be able to split
    it too; routing previews through one function means the two deployments
    cannot disagree about where a label divides.
    """
    if not label or "--" not in label:
        return (label or None), None
    slug, _, release_ref = label.rpartition("--")
    # A label that is all separator ("--r7", "orders--") names no app or no
    # release; treat it as unroutable rather than guessing which half was meant.
    if not slug or not release_ref:
        return None, None
    return slug, release_ref


def app_slug_from_host(host: str) -> tuple[str | None, str | None]:
    """Return ``(slug, release_ref)`` for ``host``, or ``(None, None)``.

    ``host`` may include a port. The label is the single left-most one in front
    of the configured ``app_base_domain``; the bare base domain (the main API
    host) and multi-level hosts are not apps.
    """
    base = settings.app_base_domain
    if not base:
        return None, None
    host_no_port = host.split(":", 1)[0].strip().lower()
    base_no_port = base.split(":", 1)[0].strip().lower()
    if not host_no_port or not base_no_port:
        return None, None
    suffix = f".{base_no_port}"
    if not host_no_port.endswith(suffix):
        return None, None
    label = host_no_port[: -len(suffix)]
    if not label or "." in label:
        return None, None
    return split_release_label(label)


class AppHostRoutingMiddleware:
    """Serve app builds via host-based routing (see module docstring)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        headers: list[tuple[bytes, bytes]] = list(scope["headers"])

        host = ""
        for key, value in headers:
            lowered = key.lower()
            if lowered == _SLUG_HEADER:
                # An upstream proxy (nginx) already resolved the slug; leave it.
                await self.app(scope, receive, send)
                return
            if lowered == b"host":
                host = value.decode("latin-1")

        slug, release_ref = app_slug_from_host(host)
        if slug is None:
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        # Real global /public routes (SDK, datastore, icons, widgets) are not app
        # assets — let them reach their own handlers instead of 404ing as a
        # missing asset of this app.
        if path.startswith(_GLOBAL_PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        new_path = _APP_PATH_PREFIX if path == "/" else _APP_PATH_PREFIX + path

        # Mutated in place rather than copied. Starlette's router records the
        # matched route by writing `scope["route"]`, and the request observer
        # that reads it sits *outside* this middleware — so with a copy the
        # router wrote to an object the observer never saw, and every app-host
        # request was logged as `route: "unmatched"`. That covered the whole
        # apps product: 52 slow 404s and 21 slow 200s in a day, none of them
        # attributable to a route on any per-route dashboard.
        new_headers = headers + [(_SLUG_HEADER, slug.encode("latin-1"))]
        if release_ref is not None:
            new_headers.append((_RELEASE_HEADER, release_ref.encode("latin-1")))
        scope["path"] = new_path
        scope["raw_path"] = new_path.encode("latin-1")
        scope["headers"] = new_headers
        await self.app(scope, receive, send)
