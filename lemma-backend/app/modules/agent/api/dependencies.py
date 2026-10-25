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
from app.modules.agent.application.conversations.read import ConversationReads
from app.modules.agent.application.conversations.start import StartConversation
from app.modules.agent.application.conversations.update import UpdateConversation
from app.modules.agent.application.pauses.decide import DecidePause
from app.modules.agent.application.turns.stop import StopConversation
from app.modules.agent.application.turns.withdraw import WithdrawQueuedMessage
from app.modules.agent.services.agent_service import AgentService
from app.modules.agent.services.service_factories import build_agent_service


def get_agent_service(uow: UoWDep) -> AgentService:
    return build_agent_service(uow)


# The conversation use cases a request-scoped route runs, each on the request's
# unit of work. Routes that open their own short unit of work (a send, a retry,
# a stream) build theirs from it with the use case's ``build``.


def get_start_conversation(uow: UoWDep) -> StartConversation:
    return StartConversation.build(uow)


def get_update_conversation(uow: UoWDep) -> UpdateConversation:
    return UpdateConversation.build(uow)


def get_conversation_reads(uow: UoWDep) -> ConversationReads:
    return ConversationReads.build(uow)


def get_stop_conversation(uow: UoWDep) -> StopConversation:
    return StopConversation.build(uow)


def get_withdraw_queued_message(uow: UoWDep) -> WithdrawQueuedMessage:
    return WithdrawQueuedMessage.build(uow)


def get_decide_pause(uow: UoWDep) -> DecidePause:
    return DecidePause.build(uow)


StartConversationDep = Annotated[StartConversation, Depends(get_start_conversation)]
UpdateConversationDep = Annotated[UpdateConversation, Depends(get_update_conversation)]
ConversationReadsDep = Annotated[ConversationReads, Depends(get_conversation_reads)]
StopConversationDep = Annotated[StopConversation, Depends(get_stop_conversation)]
WithdrawQueuedMessageDep = Annotated[
    WithdrawQueuedMessage, Depends(get_withdraw_queued_message)
]
DecidePauseDep = Annotated[DecidePause, Depends(get_decide_pause)]

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
