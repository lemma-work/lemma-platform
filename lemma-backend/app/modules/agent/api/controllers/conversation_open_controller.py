"""Opening a conversation, and the GitHub repositories one can be opened in.

Split from ``conversation_controller``, which is at the size ratchet. Included
ahead of it: ``/repositories`` would otherwise be read as a conversation id by
``GET /{conversation_id}``.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.core.api.dependencies import CurrentUser, UoWDep
from app.core.infrastructure.db.transaction_locks import connection_released
from app.modules.agent.api.controllers.conversation_controller import (
    CONVERSATION_MEMBERSHIP,
)
from app.modules.agent.api.controllers.shared import conversation_response
from app.modules.agent.api.dependencies import (
    ProjectRepositoriesDep,
    StartConversationDep,
)
from app.modules.agent.api.schemas import (
    ConversationRepositoryListResponse,
    ConversationRepositoryResponse,
    ConversationResponse,
    CreateConversationRequest,
)
from app.modules.agent.services.runtime_availability import (
    check_conversation_runtime_for_pod,
)

router = APIRouter(
    prefix="/pods/{pod_id}/conversations",
    tags=["agent_conversations"],
)


@router.post(
    "",
    response_model=ConversationResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="agent.conversation.create",
    dependencies=[CONVERSATION_MEMBERSHIP],
    summary="Create Pod Agent Conversation",
    description=(
        "Create a new pod-scoped conversation. When agent_name is omitted, "
        "the conversation uses the default pod assistant. Workflow and "
        "sub-agent executions also use conversations as their external "
        "execution handle. Name a repository to work in a checkout of it: "
        "it is refused unless the organization's GitHub installation covers "
        "it for you."
    ),
)
async def create_conversation(
    pod_id: UUID,
    data: CreateConversationRequest,
    user: CurrentUser,
    start: StartConversationDep,
) -> ConversationResponse:
    # Refused when saved, with the reason, rather than at the first turn.
    await check_conversation_runtime_for_pod(
        start.uow, data.agent_runtime, pod_id=pod_id, user_id=user.id
    )
    conversation = await start.start(
        pod_id=pod_id,
        agent_name=data.agent_name,
        user_id=user.id,
        title=data.title,
        instructions=data.instructions,
        agent_runtime=data.agent_runtime,
        parent_id=data.parent_id,
        type=data.type,
        # Only tags: nothing the server decides on (``domain/conversations``).
        metadata=data.metadata,
        repository=data.project_repo(),
    )
    return await conversation_response(start.uow, conversation)


@router.get(
    "/repositories",
    response_model=ConversationRepositoryListResponse,
    operation_id="agent.conversation.repository.list",
    dependencies=[CONVERSATION_MEMBERSHIP],
    summary="List Repositories A Conversation Can Open In",
    description=(
        "The GitHub repositories you can open a conversation in: those the "
        "organization's GitHub installation covers and your account can see, "
        "through this pod's 'github' connector. Refused with the connector's "
        "own code when that connector cannot give your sandbox a checkout. "
        "An installation covering a very large organization is listed in part."
    ),
)
async def list_conversation_repositories(
    pod_id: UUID,
    user: CurrentUser,
    uow: UoWDep,
    repositories: ProjectRepositoriesDep,
) -> ConversationRepositoryListResponse:
    # Membership was read on this request's connection; GitHub is asked without
    # holding it.
    async with connection_released(uow.session):
        choices = await repositories.reachable(pod_id=pod_id, user_id=user.id)
    return ConversationRepositoryListResponse(
        items=[ConversationRepositoryResponse.of(choice) for choice in choices]
    )
