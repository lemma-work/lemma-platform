"""Messages sent while the agent was working, which it has not seen yet.

A message sent mid-run is queued until something delivers it: the in-process
harness at its next step, an Agent Host that can steer within a second or two,
and otherwise the follow-up turn once the current one ends. Until then the
person can take it back. Split from ``conversation_controller``, which is at the
size ratchet.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.core.api.dependencies import CurrentUser
from app.modules.agent.api.controllers.conversation_controller import (
    CONVERSATION_MEMBERSHIP,
)
from app.modules.agent.api.dependencies import ConversationServiceDep

router = APIRouter(
    prefix="/pods/{pod_id}/conversations",
    tags=["agent_conversations"],
)


@router.delete(
    "/{conversation_id}/messages/{message_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    operation_id="agent.conversation.message.withdraw",
    dependencies=[CONVERSATION_MEMBERSHIP],
    summary="Withdraw Queued Conversation Message",
    description=(
        "Take back a message sent while a run was working, before the agent "
        "has seen it. Only a message still queued can be withdrawn: one that a "
        "run has read, or that is already on its way to an Agent Host turn, is "
        "answered 409."
    ),
    responses={
        404: {"description": "Conversation was not found or is not visible"},
        409: {"description": "The message is no longer queued"},
    },
)
async def withdraw_queued_message(
    pod_id: UUID,
    conversation_id: UUID,
    message_id: UUID,
    user: CurrentUser,
    service: ConversationServiceDep,
) -> None:
    await service.withdraw_queued_message(
        conversation_id=conversation_id,
        message_id=message_id,
        user_id=user.id,
        pod_id=pod_id,
    )
