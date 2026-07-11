"""Browser runtime-config injection for served HTML (apps and widgets).

An app and a conversation widget are the same primitive — a pod-authenticated
HTML page that reads ``window.__LEMMA_CONFIG__`` and talks to the pod through the
browser SDK. The host injects that config at serve time so the artifact bakes in
nothing and is portable between contexts (a widget's source fragment can be promoted
to an app without modification). This module is the shared kernel both serving paths
use; it operates on a ``pod_id``, not on any app/widget entity.
"""

from __future__ import annotations

import hashlib
import json
from uuid import UUID

from app.core.config import settings

# Sentinel attribute marking the injected <script>. Idempotency keys off this,
# NOT the bare global name — a page that merely *reads* window.__LEMMA_CONFIG__
# must still receive injection.
RUNTIME_CONFIG_SENTINEL = "data-lemma-runtime-config"


def build_runtime_config(pod_id: UUID | str) -> dict[str, str]:
    """Pod context handed to the browser SDK at serve time.

    No-build pages bake nothing in; the SDK's resolveConfig prefers this
    ``window.__LEMMA_CONFIG__`` global over env, so the host is the single source
    of truth for which pod/api/auth a served page talks to.
    """
    return {
        "podId": str(pod_id),
        "apiUrl": settings.api_url,
        "authUrl": settings.auth_frontend_url,
    }


def runtime_config_token(pod_id: UUID | str) -> str:
    """Short, stable hash of the runtime config, for cache busting (ETags)."""
    payload = json.dumps(build_runtime_config(pod_id), sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def inject_runtime_config(content: bytes | str, pod_id: UUID | str) -> bytes:
    """Insert the ``__LEMMA_CONFIG__`` script into an HTML entrypoint.

    Idempotent via the ``data-lemma-runtime-config`` sentinel attribute. Config
    values are JSON-encoded and ``<``-escaped so they cannot break out of the
    script element. Non-text content is returned unchanged.
    """
    if isinstance(content, bytes):
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            return content
    else:
        text = content

    if RUNTIME_CONFIG_SENTINEL in text:
        return text.encode("utf-8")

    payload = json.dumps(build_runtime_config(pod_id)).replace("<", "\\u003c")
    script = (
        f"<script {RUNTIME_CONFIG_SENTINEL}>window.__LEMMA_CONFIG__={payload};</script>"
    )

    lowered = text.lower()
    head_idx = lowered.find("<head")
    if head_idx != -1:
        tag_end = text.find(">", head_idx)
        if tag_end != -1:
            text = text[: tag_end + 1] + script + text[tag_end + 1 :]
            return text.encode("utf-8")
    return (script + text).encode("utf-8")
