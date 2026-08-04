"""Connector module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.connectors.api.connector_controller import router as connector
    from app.modules.connectors.api.auth_config_controller import (
        router as auth_config,
        status_router,
    )
    from app.modules.connectors.api.connector_operation_controller import (
        org_router as operation_search,
        router as operation,
    )
    from app.modules.connectors.api.connector_trigger_controller import (
        router as trigger,
    )
    from app.modules.connectors.api.connect_request_controller import (
        router as connect_request,
        org_router,
    )
    from app.modules.connectors.api.account_controller import router as account

    return [
        connector,
        auth_config,
        status_router,
        operation,
        operation_search,
        trigger,
        connect_request,
        org_router,
        account,
    ]


module = LemmaModule(name="connectors", routers=_routers)
