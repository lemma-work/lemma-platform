"""Opening the conversation somebody is already having with an agent.

Its own module because ``conversation_controller`` is at the architecture
ratchet's per-file limit, and because this route answers a different question
from the ones there: not "make me a conversation" but "take me back to mine".
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.core.api.dependencies import CurrentUser
from app.core.authorization.dependencies import require_pod_membership
from app.modules.agent.api.dependencies import ConversationServiceDep
from app.modules.agent.api.schemas import (
    AddConversationParticipantRequest,
    ConversationParticipantListResponse,
    ConversationParticipantResponse,
    ConversationResponse,
)

router = APIRouter(prefix="/pods/{pod_id}/conversations", tags=["agent_conversations"])

CONVERSATION_MEMBERSHIP = require_pod_membership()


@router.post(
    "/open",
    response_model=ConversationResponse,
    status_code=status.HTTP_200_OK,
    operation_id="agent.conversation.open",
    dependencies=[CONVERSATION_MEMBERSHIP],
    summary="Open Pod Agent Conversation",
    description=(
        "Return the caller's ongoing conversation with an agent, opening one "
        "if there is none yet. Omit agent_name for the default pod assistant. "
        "Unlike create, calling this twice returns the same conversation: it "
        "is where a person lands when they open the agent rather than a new "
        "session each time. Archived, task, project and surface-bound "
        "conversations are never returned."
    ),
)
async def open_conversation(
    pod_id: UUID,
    user: CurrentUser,
    service: ConversationServiceDep,
    agent_name: str | None = None,
) -> ConversationResponse:
    conversation, _created = await service.open_conversation(
        pod_id=pod_id,
        agent_name=agent_name,
        user_id=user.id,
    )
    return ConversationResponse.model_validate(conversation)


@router.get(
    "/{conversation_id}/participants",
    response_model=ConversationParticipantListResponse,
    operation_id="agent.conversation.participant.list",
    dependencies=[CONVERSATION_MEMBERSHIP],
    summary="List Conversation Participants",
    description="Who is in a conversation: the people, and the agents present.",
)
async def list_participants(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    service: ConversationServiceDep,
) -> ConversationParticipantListResponse:
    participants = await service.list_participants(
        conversation_id=conversation_id, user_id=user.id, pod_id=pod_id
    )
    return ConversationParticipantListResponse(
        items=[
            ConversationParticipantResponse.model_validate(participant)
            for participant in participants
        ]
    )


@router.post(
    "/{conversation_id}/participants",
    response_model=ConversationParticipantResponse,
    status_code=status.HTTP_201_CREATED,
    operation_id="agent.conversation.participant.add",
    dependencies=[CONVERSATION_MEMBERSHIP],
    summary="Add Conversation Participant",
    description=(
        "Add one person, or one agent, to a conversation. Name exactly one of "
        "user_id or agent_name. Adding a person is a grant: every answer in "
        "the conversation is from then on said to them. Their own working "
        "stays private to them, and so does everyone else's."
    ),
)
async def add_participant(
    pod_id: UUID,
    conversation_id: UUID,
    data: AddConversationParticipantRequest,
    user: CurrentUser,
    service: ConversationServiceDep,
) -> ConversationParticipantResponse:
    participant = await service.add_participant(
        conversation_id=conversation_id,
        user_id=user.id,
        pod_id=pod_id,
        member_user_id=data.user_id,
        agent_name=data.agent_name,
    )
    return ConversationParticipantResponse.model_validate(participant)


@router.delete(
    "/{conversation_id}/participants",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="agent.conversation.participant.remove",
    dependencies=[CONVERSATION_MEMBERSHIP],
    summary="Remove Conversation Participant",
    description=(
        "Remove one person, or one agent. Name exactly one of user_id or "
        "agent_id. The person who opened the conversation cannot be removed "
        "from it."
    ),
)
async def remove_participant(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    service: ConversationServiceDep,
    user_id: UUID | None = None,
    agent_id: UUID | None = None,
) -> None:
    await service.remove_participant(
        conversation_id=conversation_id,
        user_id=user.id,
        pod_id=pod_id,
        member_user_id=user_id,
        agent_id=agent_id,
    )
