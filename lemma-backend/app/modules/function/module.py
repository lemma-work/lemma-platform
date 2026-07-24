"""Function module registration."""

from contextlib import asynccontextmanager

from app.core.registry import LemmaModule


def _routers():
    from app.modules.function.api.controllers.function_controller import (
        router as function,
    )

    from app.modules.function.api.controllers.function_runtime_controller import (
        router as function_runtime,
    )

    return [function, function_runtime]


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


module = LemmaModule(
    name="function",
    routers=_routers,
    event_routers=_event_routers,
    api_lifespans=(_close_runtime_http_clients,),
    worker_lifespans=(_close_runtime_http_clients,),
)
