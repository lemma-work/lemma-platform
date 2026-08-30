"""Serve the reserved files that make a hosted app installable.

Everything under ``/.lemma/`` on an app's origin is answered here rather than
from the app's build: the manifest, the icons, the service worker, and the page
the worker shows when the network is gone. The paths and the reasoning for the
reservation live in ``app.core.app_install``.

None of it depends on the current release -- a manifest describes the app, not
the build -- so this runs before the release lookup and its ETag is a hash of
the app's own identity. Rebuilding does not invalidate an installed icon;
renaming the app does.
"""

from __future__ import annotations

import hashlib
import json
from typing import NamedTuple

from app.core import app_install
from app.modules.apps.domain.entities import AppEntity
from app.modules.apps.services.app_icon import ICON_SIZES, render_app_icon

# Long enough to be recognisable under an icon, short enough that no launcher
# has to truncate it for us.
_SHORT_NAME_LIMIT = 12

_SERVICE_WORKER = """
// Registered only so the browser will offer to install this app: Chromium
// requires a fetch handler that can answer a navigation offline. It answers
// navigations and nothing else -- an app ships a new release whenever its
// author rebuilds, so a worker that cached app assets would serve last week's
// build to whoever installed it.
const CACHE = "lemma-app-shell";
const OFFLINE = "%(offline)s";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches.open(CACHE)
      .then((cache) => cache.add(OFFLINE))
      .then(() => self.skipWaiting())
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys()
      .then((keys) => Promise.all(
        keys.filter((key) => key !== CACHE).map((key) => caches.delete(key))
      ))
      .then(() => self.clients.claim())
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return;
  event.respondWith(
    fetch(event.request).catch(() => caches.match(OFFLINE))
  );
});
"""

_OFFLINE_PAGE = """<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>Offline</title>
    <style>
      html { color-scheme: dark }
      body { margin: 0; min-height: 100vh; display: grid; place-items: center;
        background: %(plate)s; color: #fff; text-align: center; padding: 24px;
        font: 400 15px/1.5 Inter, ui-sans-serif, -apple-system,
          BlinkMacSystemFont, "Segoe UI", sans-serif }
      .mark { display: inline-flex; height: 28px; align-items: flex-end;
        gap: 4px; margin-bottom: 20px }
      .mark i { display: block; width: 5px; border-radius: 3px;
        background: #8b7af5 }
      .mark i:nth-child(1) { height: 12px }
      .mark i:nth-child(2) { height: 19px }
      .mark i:nth-child(3) { height: 28px }
      p { margin: 0; color: rgba(255,255,255,.62); max-width: 30ch }
      strong { display: block; margin-bottom: 6px; font-weight: 500;
        color: #fff; font-size: 17px }
    </style>
  </head>
  <body>
    <div>
      <span class="mark" aria-hidden="true"><i></i><i></i><i></i></span>
      <p><strong>You are offline</strong>
      This app needs a connection. It will load as soon as you have one.</p>
    </div>
  </body>
</html>
"""


def _clean(value: str | None, fallback: str, limit: int) -> str:
    normalized = " ".join((value or "").split()) or fallback
    if len(normalized) <= limit:
        return normalized
    return f"{normalized[: limit - 1].rstrip()}…"


def _short_name(name: str) -> str:
    if len(name) <= _SHORT_NAME_LIMIT:
        return name
    head = name[:_SHORT_NAME_LIMIT].rsplit(" ", 1)[0]
    return head or name[:_SHORT_NAME_LIMIT]


def build_manifest(app: AppEntity) -> dict[str, object]:
    """The web app manifest for an app, as served on its own origin."""
    name = _clean(app.name, "Lemma app", 64)
    manifest: dict[str, object] = {
        # The origin is the app, so the origin root is its identity. A renamed
        # slug is a different address and therefore a different install, which
        # is what someone who moved the app would expect.
        "id": "/",
        "name": name,
        "short_name": _short_name(name),
        "start_url": "/",
        "scope": "/",
        "display": "standalone",
        "orientation": "any",
        "background_color": app_install.PLATE_COLOR,
        "theme_color": app_install.PLATE_COLOR,
        "icons": [
            {
                "src": app_install.ICON_PATH_TEMPLATE.format(size=size),
                "sizes": f"{size}x{size}",
                "type": "image/png",
                # The icon is drawn full-bleed with its glyph inside the safe
                # circle, so one file serves both purposes.
                "purpose": "any maskable",
            }
            for size in (192, 512)
        ],
    }
    description = _clean(app.description, "", 160)
    if description:
        manifest["description"] = description
    return manifest


def reserved_asset_name(normalized_asset_path: str) -> str | None:
    """The reserved file a path asks for, or None if the path is the app's own.

    An app that genuinely ships its own ``.lemma/`` directory keeps every name
    this module does not claim: an unrecognised one falls through and is looked
    up in the build like any other asset.
    """
    prefix = app_install.RESERVED_ASSET_PREFIX
    if not normalized_asset_path.startswith(prefix):
        return None
    name = normalized_asset_path[len(prefix) :]
    known = {"manifest.webmanifest", "sw.js", "offline.html"}
    known.update(f"icon-{size}.png" for size in ICON_SIZES)
    return name if name in known else None


def reserved_asset_etag(app: AppEntity, name: str) -> str:
    """A tag over what the reserved files are built from, not over the build.

    A rebuild leaves an installed icon and manifest alone; renaming the app
    replaces both, which is the only time either actually changes.
    """
    return hashlib.sha256(
        "\x00".join((app.name or "", app.public_slug or "", name)).encode("utf-8")
    ).hexdigest()[:12]


class ReservedAsset(NamedTuple):
    content: bytes
    media_type: str
    headers: dict[str, str] | None = None


def render_reserved_asset(app: AppEntity, name: str) -> ReservedAsset:
    """Build the bytes for a name ``reserved_asset_name`` returned."""
    if name == "manifest.webmanifest":
        return ReservedAsset(
            json.dumps(build_manifest(app)).encode("utf-8"),
            "application/manifest+json",
        )

    if name == "sw.js":
        body = _SERVICE_WORKER % {"offline": app_install.OFFLINE_PATH}
        return ReservedAsset(
            body.encode("utf-8"),
            "text/javascript; charset=utf-8",
            # Registered with ``{scope: "/"}`` from a script one directory down,
            # which the browser refuses without this header.
            {"Service-Worker-Allowed": "/"},
        )

    if name == "offline.html":
        body = _OFFLINE_PAGE % {"plate": app_install.PLATE_COLOR}
        return ReservedAsset(body.encode("utf-8"), "text/html; charset=utf-8")

    size = int(name.removeprefix("icon-").removesuffix(".png"))
    return ReservedAsset(
        render_app_icon(name=app.name, slug=app.public_slug, size=size),
        "image/png",
    )
