"""Workflow module dependencies."""

from typing import Annotated
from fastapi import Depends

from app.core.api.dependencies import UoWDep
from app.core.authorization.context import ResourceType
from app.core.authorization.dependencies import (
    pod_from_path,
    require_action,
    require_resource_admin_or_creator,
    require_resource_action,
)
from app.core.authorization.permissions import Permissions
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.icon.contracts.provisioning import create_icon_service
from app.composition.workflow_notifications import WorkflowNotificationAdapter
from app.modules.agent.contracts.workflow_control import build_agent_control_adapter
from app.modules.function.contracts.workflow_control import (
    build_function_control_adapter,
)
from app.modules.workflow.execution.engine import WorkflowEngine
from app.modules.workflow.execution.timers import WaitRowTimer
from app.modules.workflow.services.workflow_service import WorkflowService


def get_workflow_service(uow: UoWDep) -> WorkflowService:
    """Provide workflow service."""
    return WorkflowService(uow, icon_service=create_icon_service())


def build_workflow_engine(uow: SqlAlchemyUnitOfWork) -> WorkflowEngine:
    """An engine with its four collaborators bound, for this transaction.

    The one place that chooses them. `WorkflowEngine.__init__` used to default
    each to `None` and resolve it, so twelve call sites wrote `WorkflowEngine(uow)`
    and the binding lived at the bottom of the module rather than at its edge --
    which is how the engine came to import three other modules' adapters to run
    a workflow.
    """
    return WorkflowEngine(
        uow,
        agent_adapter=build_agent_control_adapter(uow),
        function_adapter=build_function_control_adapter(uow),
        schedule_adapter=WaitRowTimer(),
        notification_adapter=WorkflowNotificationAdapter(uow),
    )


WorkflowServiceDep = Annotated[WorkflowService, Depends(get_workflow_service)]

# Auth dependencies for controller routes
WorkflowViewerDep = require_action(Permissions.WORKFLOW_READ, pod_from_path)
WorkflowEditorDep = require_action(Permissions.WORKFLOW_UPDATE, pod_from_path)
WorkflowAdminDep = require_action(Permissions.WORKFLOW_DELETE, pod_from_path)
WorkflowExecuteDep = require_action(Permissions.WORKFLOW_EXECUTE, pod_from_path)
WorkflowResourceViewerDep = require_resource_action(
    Permissions.WORKFLOW_READ,
    resource_type=ResourceType.WORKFLOW,
    name_param="workflow_name",
)
WorkflowResourceEditorDep = require_resource_action(
    Permissions.WORKFLOW_UPDATE,
    resource_type=ResourceType.WORKFLOW,
    name_param="workflow_name",
)
WorkflowResourceAdminDep = require_resource_action(
    Permissions.WORKFLOW_DELETE,
    resource_type=ResourceType.WORKFLOW,
    name_param="workflow_name",
)
WorkflowResourceDeleteDep = require_resource_admin_or_creator(
    Permissions.WORKFLOW_DELETE,
    resource_type=ResourceType.WORKFLOW,
    name_param="workflow_name",
)
WorkflowResourceExecuteDep = require_resource_action(
    Permissions.WORKFLOW_EXECUTE,
    resource_type=ResourceType.WORKFLOW,
    name_param="workflow_name",
)
