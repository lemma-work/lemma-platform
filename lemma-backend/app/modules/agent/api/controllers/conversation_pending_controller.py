"""What is waiting on the caller, across their conversations in a pod.

The web client's "waiting on you" used to list WAITING conversations and then
ask each one for its approvals: a request per conversation, and blind to an
Agent Host permission request, whose conversation stays RUNNING. This is that
question answered once. Nothing is pushed and nothing reminds -- the list is
where the person looks.

Split from ``conversation_controller``, which is at the size ratchet. Included
ahead of it: ``/pending-interactions`` would otherwise be read as a
conversation id by ``GET /{conversation_id}``.
"""

from __future__ import annotations

import base64
import binascii
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query, status

from app.core.api.dependencies import CurrentUser
from app.modules.agent.api.controllers.conversation_controller import (
    CONVERSATION_ENUMERATION,
)
from app.modules.agent.api.dependencies import ConversationReadsDep
from app.modules.agent.api.schemas import (
    PendingInteractionListResponse,
    PendingInteractionResponse,
)
from app.modules.agent.domain.pending_interactions import PendingInteractionCursor

router = APIRouter(
    prefix="/pods/{pod_id}/conversations",
    tags=["agent_conversations"],
)


def encode_pending_page_token(cursor: PendingInteractionCursor) -> str:
    raw = f"{cursor.created_at.isoformat()}|{cursor.message_id}".encode()
    return base64.urlsafe_b64encode(raw).decode("ascii")


def parse_pending_page_token(page_token: str | None) -> PendingInteractionCursor | None:
    """Opaque on the wire; ``created_at|message_id`` inside, the list's order."""
    if not page_token:
        return None
    try:
        decoded = base64.urlsafe_b64decode(page_token.encode("ascii")).decode()
        raw_created_at, raw_id = decoded.split("|", 1)
        created_at = datetime.fromisoformat(raw_created_at)
        message_id = UUID(raw_id)
    except (ValueError, UnicodeError, binascii.Error) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid page_token"
        ) from exc
    if created_at.tzinfo is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid page_token"
        )
    return PendingInteractionCursor(created_at=created_at, message_id=message_id)


@router.get(
    "/pending-interactions",
    response_model=PendingInteractionListResponse,
    operation_id="agent.conversation.pending_interaction.list",
    dependencies=[CONVERSATION_ENUMERATION],
    summary="List Interactions Waiting On You",
    description=(
        "List every ask_user question, request_approval and Agent Host "
        "permission request waiting on the caller, across their own "
        "conversations in the pod, sub-agent conversations included and "
        "archived ones left out. "
        "Longest-waiting first. Resolve one through the conversation's "
        "approval decision endpoint, using tool_call_id as the approval id. "
        "A permission request drops off once the Agent Host has stopped "
        "waiting for it."
    ),
)
async def list_pending_interactions(
    pod_id: UUID,
    user: CurrentUser,
    reads: ConversationReadsDep,
    page_token: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
) -> PendingInteractionListResponse:
    pending, next_cursor = await reads.list_pending_interactions(
        pod_id=pod_id,
        user_id=user.id,
        cursor=parse_pending_page_token(page_token),
        limit=limit,
    )
    return PendingInteractionListResponse(
        items=[PendingInteractionResponse.model_validate(item) for item in pending],
        limit=limit,
        next_page_token=(
            encode_pending_page_token(next_cursor) if next_cursor else None
        ),
    )
