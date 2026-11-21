"""One run of a conversation, with the model tokens it used.

A plugin meters what an item cost by reading its run back. Split from
``conversation_controller``, which is at the size ratchet; the path is two
segments under a conversation, so the order it is included in does not matter.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.core.api.dependencies import CurrentUser
from app.modules.agent.api.controllers.conversation_controller import (
    CONVERSATION_MEMBERSHIP,
)
from app.modules.agent.api.dependencies import ConversationReadsDep
from app.modules.agent.api.run_schemas import AgentRunResponse

router = APIRouter(
    prefix="/pods/{pod_id}/conversations",
    tags=["agent_conversations"],
)


@router.get(
    "/{conversation_id}/runs/{run_id}",
    response_model=AgentRunResponse,
    operation_id="agent.conversation.run.get",
    dependencies=[CONVERSATION_MEMBERSHIP],
    summary="Get Conversation Run",
    description=(
        "One run of a conversation: its status, output and error, and the model "
        "tokens it used (input, output, cache reads and writes, and the models). "
        "A conversation's latest run is `last_run_id` on the conversation; a "
        "send's is `agent_run_id` on its answer. 404 for a run of any other "
        "conversation."
    ),
)
async def get_conversation_run(
    pod_id: UUID,
    conversation_id: UUID,
    run_id: UUID,
    user: CurrentUser,
    reads: ConversationReadsDep,
) -> AgentRunResponse:
    run, usage = await reads.get_run(
        conversation_id=conversation_id,
        agent_run_id=run_id,
        user_id=user.id,
        pod_id=pod_id,
    )
    return AgentRunResponse.of(run, usage)
