"""The element picker an app carries so it can be edited from the pod shell.

An app runs on its own subdomain, framed by the pod shell. Cross-origin means
the shell cannot read the app's DOM, so "click this element and tell the agent
what to change about it" has to be answered by code running inside the app.
That code is :mod:`app.core.assets.lemma-app-editor-bridge` (plain browser JS,
injected verbatim); this module decides who is allowed to drive it and wraps it
for injection.

The allowlist is the security boundary. The bridge reads the rendered page and
hands it to whichever window framed it, so without an origin check any site
could embed a public app and read back whatever the viewer's session renders.
"""

from __future__ import annotations

import hashlib
import html
import json
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from app.core.config import settings

# Sentinel attribute marking the injected <script>, so injection is idempotent
# for a document that already carries the bridge.
EDITOR_BRIDGE_SENTINEL = "data-lemma-editor-bridge"

_BRIDGE_PATH = Path(__file__).parent / "assets" / "lemma-app-editor-bridge.js"


@lru_cache(maxsize=1)
def _bridge_source() -> str:
    # The asset ships with the backend and never changes at runtime, so it is
    # read once rather than per served entrypoint.
    return _BRIDGE_PATH.read_text(encoding="utf-8")


def _origin_of(url: str | None) -> str | None:
    if not url:
        return None
    parsed = urlparse(url)
    if not parsed.scheme or not parsed.netloc:
        return None
    return f"{parsed.scheme}://{parsed.netloc}"


def editor_origins() -> list[str]:
    """Origins allowed to drive an app's element picker.

    The CORS allowlist plus the frontend itself. A browser origin this
    deployment already trusts to make credentialed API calls is, by
    construction, at least as privileged as one allowed to read a framed app's
    DOM — so it is the right list, and it covers the desktop app's ``tauri://``
    origin without a second place to configure. A ``*`` entry is dropped: as a
    CORS value it permits unauthenticated reads, which is not the same claim as
    "any embedder may read what this viewer sees".
    """
    origins: list[str] = []
    candidates = [_origin_of(settings.frontend_url), *settings.cors_origins]
    for candidate in candidates:
        value = (candidate or "").strip()
        if not value or value == "*" or value in origins:
            continue
        origins.append(value)
    return origins


def editor_bridge_script() -> str:
    """The bridge as a ``<script>`` element, or ``""`` when nothing may drive it."""
    origins = editor_origins()
    if not origins:
        return ""
    allowlist = html.escape(json.dumps(origins), quote=True)
    # `</script` inside the body would close the element early. The asset does
    # not contain it today; escaping means a future edit cannot break the page.
    source = _bridge_source().replace("</script", "<\\/script")
    return (
        f'<script {EDITOR_BRIDGE_SENTINEL} data-lemma-editor-origins="{allowlist}">'
        f"{source}</script>"
    )


def editor_bridge_fingerprint() -> str:
    """Short hash of the bridge and its allowlist, for entrypoint ETags.

    Entrypoints are cached against a token covering everything injected into
    them. Without this, a viewer holding a cached page would keep running the
    previous bridge — including its previous idea of who may drive it.
    """
    payload = json.dumps(
        {"origins": editor_origins(), "source": _bridge_source()}, sort_keys=True
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]
