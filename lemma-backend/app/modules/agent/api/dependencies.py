"""Agent FastAPI dependencies."""

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
from app.modules.agent.services.agent_service import AgentService
from app.modules.agent.services.conversation_service import ConversationService
from app.modules.agent.services.service_factories import (
    build_agent_service,
    build_conversation_service,
)


def get_conversation_service(
    uow: UoWDep,
) -> ConversationService:
    return build_conversation_service(uow)


def get_agent_service(uow: UoWDep) -> AgentService:
    return build_agent_service(uow)


ConversationServiceDep = Annotated[
    ConversationService,
    Depends(get_conversation_service),
]

AgentServiceDep = Annotated[
    AgentService,
    Depends(get_agent_service),
]

AgentViewerDep = require_action(Permissions.AGENT_READ, pod_from_path)
AgentEditorDep = require_action(Permissions.AGENT_UPDATE, pod_from_path)
AgentAdminDep = require_action(Permissions.AGENT_DELETE, pod_from_path)
AgentExecuteDep = require_action(Permissions.AGENT_EXECUTE, pod_from_path)
AgentResourceViewerDep = require_resource_action(
    Permissions.AGENT_READ,
    resource_type=ResourceType.AGENT,
    name_param="agent_name",
)
AgentResourceEditorDep = require_resource_action(
    Permissions.AGENT_UPDATE,
    resource_type=ResourceType.AGENT,
    name_param="agent_name",
)
AgentResourceDeleteDep = require_resource_admin_or_creator(
    Permissions.AGENT_DELETE,
    resource_type=ResourceType.AGENT,
    name_param="agent_name",
)
ConversationViewerDep = require_action(Permissions.CONVERSATION_READ, pod_from_path)
ConversationWriterDep = require_action(Permissions.CONVERSATION_WRITE, pod_from_path)
