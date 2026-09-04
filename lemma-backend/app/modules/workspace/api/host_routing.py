"""Host-based routing for the sandbox browser.

A browser is served at ``<code>.<browser_base_domain>``. This middleware reads
the ``Host`` header and, when it names a browser, rewrites the request onto the
signed port proxy — so the dashboard's absolute asset paths (``/_next/...``)
resolve against its own origin instead of the API root, which is the only way a
Next.js app can be proxied at all.

The same shape as ``apps/api/host_routing.py``, and for the same reason: a
served application owns every path on its origin, so the only thing that can
distinguish it is the host.
"""

from __future__ import annotations

from starlette.types import ASGIApp, Receive, Scope, Send

from app.modules.workspace.services.browser_host import (
    BrowserHostCodeStore,
    browser_code_from_host,
)

_PROXY_PREFIX = "/workspace-ports"


class BrowserHostRoutingMiddleware:
    """Serve a sandbox browser by host (see module docstring)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self._codes = BrowserHostCodeStore()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        # Both, because the dashboard's live view is a WebSocket and it is
        # opened against the same origin the page came from.
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        host = ""
        for key, value in scope["headers"]:
            if key.lower() == b"host":
                host = value.decode("latin-1")
                break

        code = browser_code_from_host(host)
        if code is None:
            await self.app(scope, receive, send)
            return

        token = await self._codes.resolve(code)
        if token is None:
            # Expired or never existed — indistinguishable on purpose, as on the
            # proxy itself.
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        rewritten = f"{_PROXY_PREFIX}/{token}{path if path != '/' else '/'}"

        # Mutated in place rather than copied, for the reason the app host
        # middleware records: the router writes `scope["route"]`, and an
        # observer outside this middleware reads it — a copy makes every
        # browser-host request log as unmatched.
        scope["path"] = rewritten
        scope["raw_path"] = rewritten.encode("utf-8")
        await self.app(scope, receive, send)
