"""Workspace module registration."""

from contextlib import asynccontextmanager

from app.core.registry import LemmaModule


def _routers():
    from app.modules.workspace.api.controllers.workspace_controller import (
        router as workspace,
    )
    from app.modules.workspace.api.controllers.browser_controller import (
        router as browser,
    )

    return [workspace, browser]


@asynccontextmanager
async def _close_workspace_clients(app):
    del app
    try:
        yield
    finally:
        from app.modules.workspace.services.workspace_sandbox_service import (
            reset_workspace_store_state,
        )
        from app.modules.workspace.services.workspace_tool_runtime import (
            close_workspace_tool_runtimes,
        )

        await close_workspace_tool_runtimes()
        await reset_workspace_store_state()


module = LemmaModule(
    name="workspace",
    routers=_routers,
    api_lifespans=(_close_workspace_clients,),
)
