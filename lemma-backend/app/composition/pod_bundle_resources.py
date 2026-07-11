"""Lazy resource service bindings for pod bundle contributors.

The indirection keeps module composition testable: module-level factories can be
replaced before a contributor resolves them, and optional surface imports do not
load during bundle module registration.
"""


def build_app_service(*args, **kwargs):
    from app.modules.apps.api.dependencies import build_app_service as factory

    return factory(*args, **kwargs)


def build_connector_operation_service(*args, **kwargs):
    from app.modules.connectors.api.dependencies import (
        build_connector_operation_service as factory,
    )

    return factory(*args, **kwargs)


def get_connector_service(*args, **kwargs):
    from app.modules.connectors.api.dependencies import get_connector_service as factory

    return factory(*args, **kwargs)


def build_file_service(*args, **kwargs):
    from app.modules.datastore.api.dependencies import build_file_service as factory

    return factory(*args, **kwargs)


def build_record_service(*args, **kwargs):
    from app.modules.datastore.api.dependencies import build_record_service as factory

    return factory(*args, **kwargs)


def build_table_service(*args, **kwargs):
    from app.modules.datastore.api.dependencies import build_table_service as factory

    return factory(*args, **kwargs)


def build_function_service(*args, **kwargs):
    from app.modules.function.api.dependencies import build_function_service as factory

    return factory(*args, **kwargs)


def get_agent_service(*args, **kwargs):
    from app.modules.agent.api.dependencies import get_agent_service as factory

    return factory(*args, **kwargs)


def get_schedule_service(*args, **kwargs):
    from app.modules.schedule.api.dependencies import get_schedule_service as factory

    return factory(*args, **kwargs)


def get_workflow_service(*args, **kwargs):
    from app.modules.workflow.api.dependencies import get_workflow_service as factory

    return factory(*args, **kwargs)


def get_surface_service(*args, **kwargs):
    from app.modules.agent_surfaces.api.dependencies import get_surface_service as factory

    return factory(*args, **kwargs)


def _merge_surface_config(*args, **kwargs):
    from app.modules.agent_surfaces.api.controllers.surface_controller import (
        _merge_surface_config as operation,
    )

    return operation(*args, **kwargs)


def _resolve_surface_config(*args, **kwargs):
    from app.modules.agent_surfaces.api.controllers.surface_controller import (
        _resolve_surface_config as operation,
    )

    return operation(*args, **kwargs)


def _surface_response(*args, **kwargs):
    from app.modules.agent_surfaces.api.controllers.surface_controller import (
        _surface_response as operation,
    )

    return operation(*args, **kwargs)
