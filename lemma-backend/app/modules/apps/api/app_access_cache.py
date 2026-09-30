"""Do not cache handoff errors emitted before a controller can add headers."""

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from app.modules.apps.api.host_routing import app_label_from_host


class AppAccessCacheMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        handoff = path.startswith(
            ("/apps/access/", "/_lemma/app-access/", "/public/apps/_lemma/app-access/")
        )
        host = next(
            (
                value.decode("latin-1")
                for key, value in scope["headers"]
                if key.lower() == b"host"
            ),
            "",
        )
        app_host = app_label_from_host(host) is not None
        if not handoff and not app_host:
            await self.app(scope, receive, send)
            return

        async def private_headers(message: Message) -> None:
            if message["type"] == "http.response.start" and (
                handoff or message["status"] >= 400
            ):
                message["headers"] = [
                    (key, value)
                    for key, value in message["headers"]
                    if key.lower() != b"cache-control"
                ] + [(b"cache-control", b"private, no-store")]
            await send(message)

        await self.app(scope, receive, private_headers)
