"""Managed-local Lemma application entrypoint.

This is the two-process desktop/backend topology: one Python process owns the
API, Streaq worker, scheduler, and AgentBox manager, while the Next frontend is
the only other Lemma application process. Infrastructure and sandbox compute
remain outside this process.
"""

from __future__ import annotations

from fastapi import FastAPI
from starlette.types import ASGIApp, Receive, Scope, Send

from agentbox.api.app import create_app as create_agentbox_app
from agentbox.api.apps import sandbox_app_from_host
from app.app import create_app as create_api_app
from app.standalone import EmbeddedApp, build_standalone_app

AGENTBOX_MOUNT_PATH = "/internal/agentbox"


class AgentBoxHostRoutingMiddleware:
    """Dispatch workspace app hosts to the embedded AgentBox proxy.

    Manager APIs remain under ``/internal/agentbox``. Only a validated
    ``<sandbox>-<app>.<workspace-domain>`` host is dispatched at the root,
    keeping built pod app hosts on the normal Lemma application router.
    """

    def __init__(self, app: ASGIApp, *, agentbox_app: ASGIApp) -> None:
        self.app = app
        self.agentbox_app = agentbox_app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        host = next(
            (
                value.decode("latin-1")
                for key, value in scope.get("headers", ())
                if key.lower() == b"host"
            ),
            "",
        )
        if sandbox_app_from_host(host) is None:
            await self.app(scope, receive, send)
            return

        manager_scope = dict(scope)
        manager_scope["app"] = self.agentbox_app
        manager_scope["root_path"] = ""
        await self.agentbox_app(manager_scope, receive, send)


def create_local_app() -> FastAPI:
    from app.events import streaq_worker

    agentbox_app = create_agentbox_app(shutdown_process_telemetry=False)
    local_app = build_standalone_app(
        create_api_app(),
        streaq_worker,
        embedded_apps=(EmbeddedApp(AGENTBOX_MOUNT_PATH, agentbox_app),),
    )
    local_app.add_middleware(
        AgentBoxHostRoutingMiddleware,
        agentbox_app=agentbox_app,
    )
    local_app.state.embedded_agentbox = True
    return local_app


app = create_local_app()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "local_app:app",
        host="0.0.0.0",
        port=8711,
        reload=False,
        ws="websockets-sansio",
        access_log=False,
    )
