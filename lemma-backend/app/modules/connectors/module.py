"""Connector module registration."""

from app.core.registry import LemmaModule


def _routers():
    from app.modules.connectors.api.account_controller import router as account
    from app.modules.connectors.api.auth_config_controller import (
        router as auth_config,
    )
    from app.modules.connectors.api.auth_config_controller import (
        status_router,
    )
    from app.modules.connectors.api.connect_request_controller import (
        org_router,
    )
    from app.modules.connectors.api.connect_request_controller import (
        router as connect_request,
    )
    from app.modules.connectors.api.connector_controller import router as connector
    from app.modules.connectors.api.connector_operation_controller import (
        org_router as operation_search,
    )
    from app.modules.connectors.api.connector_operation_controller import (
        router as operation,
    )
    from app.modules.connectors.api.connector_trigger_controller import (
        router as trigger,
    )
    from app.modules.connectors.api.pod_connector_controller import (
        installation_router,
        me_router,
    )
    from app.modules.connectors.api.pod_connector_controller import (
        router as pod_connector,
    )

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
        pod_connector,
        me_router,
        installation_router,
    ]


def _register_periodic_work() -> None:
    import app.modules.connectors.events.tasks  # noqa: F401


def _register_work() -> None:
    # Every process: a member leaves a pod (API) and their pod accounts go.
    import app.modules.connectors.events.member_left_work  # noqa: F401


def _resource_names():
    """How grants name pod connectors (by slug) and pod accounts (by id).

    Declared here rather than special-cased in `app/core`, which used to map a
    connector's catalog id to a uuid5 and an account to its raw org id.
    """
    from app.core.authorization.context import ResourceType
    from app.core.authorization.resource_names import ResourceNameTable
    from app.modules.connectors.infrastructure.models.pod_connector import (
        PodAccount,
        PodConnector,
    )

    return [
        (
            ResourceType.CONNECTOR,
            ResourceNameTable(PodConnector.id, PodConnector.pod_id, PodConnector.slug),
        ),
        (
            ResourceType.CONNECTOR_ACCOUNT,
            # A pod account has no name of its own; its id is what grants
            # name it by.
            ResourceNameTable(PodAccount.id, PodAccount.pod_id, PodAccount.id),
        ),
    ]


module = LemmaModule(
    name="connectors",
    routers=_routers,
    register_work=_register_work,
    register_periodic_work=_register_periodic_work,
    resource_names=_resource_names,
)
