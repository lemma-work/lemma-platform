"""Lazy app and workspace adapters for bundle jobs."""


class WorkspaceSandboxService:
    def __new__(cls, *args, **kwargs):
        from app.modules.workspace.services.workspace_sandbox_service import (
            WorkspaceSandboxService as implementation,
        )

        return implementation(*args, **kwargs)


def build_app_service(*args, **kwargs):
    from app.modules.apps.api.dependencies import build_app_service as factory

    return factory(*args, **kwargs)


async def invalidate_function_workspace_env_cache(*args, **kwargs):
    from app.modules.workspace.services.workspace_tool_runtime import (
        invalidate_function_workspace_env_cache as operation,
    )

    return await operation(*args, **kwargs)
