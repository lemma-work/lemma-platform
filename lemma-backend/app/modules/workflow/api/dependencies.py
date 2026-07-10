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
from app.composition.icons import create_icon_service
from app.modules.workflow.services.workflow_service import WorkflowService


def get_workflow_service(uow: UoWDep) -> WorkflowService:
    """Provide workflow service."""
    return WorkflowService(uow, icon_service=create_icon_service())


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
