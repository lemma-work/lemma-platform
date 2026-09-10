"""Function module registration."""

from contextlib import asynccontextmanager

from app.core.registry import LemmaModule


def _routers():
    from app.modules.function.api.controllers.function_controller import (
        router as function,
    )

    from app.modules.function.api.controllers.function_revision_controller import (
        router as function_revision,
    )

    from app.modules.function.api.controllers.function_runtime_controller import (
        router as function_runtime,
    )

    return [function, function_revision, function_runtime]


def _event_routers():
    # Importing this module registers the function Streaq task and reconciler.
    from app.modules.function.events.handlers import router

    return [router]


@asynccontextmanager
async def _close_runtime_http_clients(context):
    del context
    try:
        yield
    finally:
        from app.modules.function.api.dependencies import (
            close_function_runtime_http_clients,
        )

        await close_function_runtime_http_clients()


def _resource_names():
    """How this module's resources are addressed by name in a grant.

    A thunk so the ORM import happens at assembly rather than whenever the
    module registry is imported. `app/core/authorization/resource_names.py`
    used to hold this table for every module at once.
    """
    from app.core.authorization.context import ResourceType
    from app.core.authorization.resource_names import ResourceNameTable
    from app.modules.function.infrastructure.models import FunctionModel

    return (
        (
            ResourceType.FUNCTION,
            ResourceNameTable(
                FunctionModel.id, FunctionModel.pod_id, FunctionModel.name
            ),
        ),
    )


module = LemmaModule(
    name="function",
    resource_names=_resource_names,
    routers=_routers,
    event_routers=_event_routers,
    api_lifespans=(_close_runtime_http_clients,),
    worker_lifespans=(_close_runtime_http_clients,),
)
