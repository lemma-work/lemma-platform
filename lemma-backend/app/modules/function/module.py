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


def _register_periodic_work() -> None:
    # Run and revision retention.
    import app.modules.function.events.handlers  # noqa: F401


def _register_work() -> None:
    # ``function.run`` / ``function.cancel`` / ``function.deadline``. The API
    # writes them (creating, starting and cancelling runs), the worker runs them.
    import app.modules.function.events.run_work  # noqa: F401


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
    register_work=_register_work,
    register_periodic_work=_register_periodic_work,
    api_lifespans=(_close_runtime_http_clients,),
    worker_lifespans=(_close_runtime_http_clients,),
)
