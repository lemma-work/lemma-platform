"""Calls module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.calls.api.controllers.call_controller import (
        router as calls,
        socket_router as call_socket,
    )

    return [calls, call_socket]


module = LemmaModule(name="calls", routers=_routers)
