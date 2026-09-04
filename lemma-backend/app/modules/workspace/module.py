"""Workspace module registration."""

from contextlib import asynccontextmanager

from app.core.registry import LemmaModule
from app.modules.workspace.api.host_routing import BrowserHostRoutingMiddleware


def _routers():
    from app.modules.workspace.api.controllers.browser_controller import (
        router as browser,
    )
    from app.modules.workspace.api.controllers.browser_stream_controller import (
        router as browser_stream,
    )
    from app.modules.workspace.api.controllers.files_controller import (
        router as files,
    )
    from app.modules.workspace.api.controllers.port_proxy_controller import (
        router as port_proxy,
    )
    from app.modules.workspace.api.controllers.takeover_controller import (
        router as takeover,
    )

    return [browser, browser_stream, files, takeover, port_proxy]


def _register_streaq() -> None:
    import app.modules.workspace.events.tasks  # noqa: F401


@asynccontextmanager
async def _close_workspace_clients(app):
    del app
    try:
        yield
    finally:
        from app.modules.workspace.services.sandbox_composition import (
            reset_sandbox_service,
        )
        from app.modules.workspace.services.workspace_sandbox_service import (
            reset_workspace_store_state,
        )
        from app.modules.workspace.services.workspace_tool_runtime import (
            close_workspace_tool_runtimes,
        )

        await close_workspace_tool_runtimes()
        await reset_workspace_store_state()
        await reset_sandbox_service()


module = LemmaModule(
    name="workspace",
    routers=_routers,
    middlewares=(BrowserHostRoutingMiddleware,),
    register_streaq=_register_streaq,
    api_lifespans=(_close_workspace_clients,),
)
