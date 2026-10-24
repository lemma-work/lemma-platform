"""Workspace module registration."""

from contextlib import asynccontextmanager

from app.core.registry import LemmaModule


def _routers():
    from app.modules.workspace.api.controllers.machine_browser_controller import (
        router as browser,
    )
    from app.modules.workspace.api.controllers.machine_browser_view_controller import (
        router as browser_view,
    )
    from app.modules.workspace.api.controllers.machine_consents_controller import (
        router as consents,
    )
    from app.modules.workspace.api.controllers.machine_files_controller import (
        router as files,
    )
    from app.modules.workspace.api.controllers.machine_preview_proxy import (
        router as preview_proxy,
    )
    from app.modules.workspace.api.controllers.machine_previews_controller import (
        router as previews,
    )
    from app.modules.workspace.api.controllers.machine_processes_controller import (
        router as processes,
    )
    from app.modules.workspace.api.controllers.machines_controller import (
        router as machines,
    )
    from app.modules.workspace.api.controllers.port_proxy_controller import (
        router as port_proxy,
    )
    from app.modules.workspace.api.controllers.browser_view_controller import (
        router as old_browser_view,
    )
    from app.modules.workspace.api.controllers.workspace_shims_controller import (
        apps_router as old_browser_access,
    )
    from app.modules.workspace.api.controllers.workspace_shims_controller import (
        router as old_workspace,
    )

    # `consents` and `previews` before `machines`: their literal paths must be
    # matched before `/machines/{ref}` reads them as a ref.
    return [
        consents,
        previews,
        machines,
        processes,
        files,
        browser,
        browser_view,
        preview_proxy,
        port_proxy,
        # `/workspace/*`, deprecated: `/machines/me` under the old paths.
        old_workspace,
        old_browser_access,
        old_browser_view,
    ]


def _resource_names():
    """Pod machines are named in grants by their name in the pod."""
    from app.core.authorization.context import ResourceType
    from app.core.authorization.resource_names import ResourceNameTable
    from app.modules.workspace.infrastructure.models import SandboxModel

    return (
        (
            ResourceType.MACHINE,
            ResourceNameTable(
                SandboxModel.id,
                SandboxModel.owner_id,
                SandboxModel.slug,
                (
                    SandboxModel.kind == "workspace",
                    SandboxModel.owner_kind == "pod",
                    SandboxModel.desired_state != "deleted",
                ),
            ),
        ),
    )


def _register_periodic_work() -> None:
    import app.modules.workspace.events.tasks  # noqa: F401
    import app.modules.workspace.infrastructure.sandbox_gauge  # noqa: F401


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
    resource_names=_resource_names,
    register_periodic_work=_register_periodic_work,
    api_lifespans=(_close_workspace_clients,),
)
