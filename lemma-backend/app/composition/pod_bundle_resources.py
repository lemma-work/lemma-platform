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


async def sync_agent_memory_grant(*args, **kwargs):
    """The `/memory` folder and grant that an agent's MEMORY toolset implies.

    Here rather than imported directly by the bundle applier, because
    `pod_bundle -> agent` is a forbidden edge: modules reach each other through
    this composition layer, the same way `get_agent_service` above does.

    `sync_memory_folder_grant` lived only in the agent HTTP controller, so an
    agent created straight through the service -- which is what the applier does
    -- got the toolset without the folder it writes to or the grant that makes it
    writable. Invisible until MEMORY became a default for new agents (#476);
    after that, exporting a pod and importing it back failed outright. The source
    agent held `folder:/memory`, the export recorded the grant, and applying it
    against a pod where nothing had created that folder raised
    `400: Unknown resource name(s): folder:/memory`, which the apply handler
    reported only as "Apply failed due to a transient error."

    The applier calls it after create/update and again after the deferred grants
    step: an inline grant list *replaces* every grant the agent holds, so a
    derived one applied first is the first thing wiped. That is the ordering the
    agent controller documents on its own two call sites.

    The applier's *pre*-replace call is gone: `AgentService.create_agent` now
    derives it for every caller, so only the re-derivation is the applier's own
    business.
    """
    from app.modules.agent.services.agent_memory_grant import (
        sync_memory_folder_grant as factory,
    )

    return await factory(*args, **kwargs)


def get_schedule_service(*args, **kwargs):
    from app.modules.schedule.api.dependencies import get_schedule_service as factory

    return factory(*args, **kwargs)


def get_workflow_service(*args, **kwargs):
    from app.modules.workflow.api.dependencies import get_workflow_service as factory

    return factory(*args, **kwargs)


def get_surface_service(*args, **kwargs):
    from app.modules.agent_surfaces.api.dependencies import (
        get_surface_service as factory,
    )

    return factory(*args, **kwargs)


def _merge_surface_config(*args, **kwargs):
    from app.modules.agent_surfaces.api.surface_config_resolver import (
        merge_surface_config as operation,
    )

    return operation(*args, **kwargs)


def _resolve_surface_config(*args, **kwargs):
    from app.modules.agent_surfaces.api.surface_config_resolver import (
        resolve_surface_config as operation,
    )

    return operation(*args, **kwargs)


def _surface_response(*args, **kwargs):
    from app.modules.agent_surfaces.api.controllers.surface_controller import (
        _surface_response as operation,
    )

    return operation(*args, **kwargs)
