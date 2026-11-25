"""One run of a conversation with the model tokens it used, and what the whole
conversation has cost.

A plugin meters what an item cost by reading its run back. Split from
``conversation_controller``, which is at the size ratchet; the paths are under
a conversation and below it, so the order they are included in does not matter.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.core.api.dependencies import CurrentUser
from app.modules.agent.api.controllers.conversation_controller import (
    CONVERSATION_MEMBERSHIP,
)
from app.modules.agent.api.dependencies import ConversationReadsDep
from app.modules.agent.api.run_schemas import (
    AgentRunResponse,
    ConversationUsageResponse,
)

router = APIRouter(
    prefix="/pods/{pod_id}/conversations",
    tags=["agent_conversations"],
)

#: How many runs the usage route lists one by one. Its totals cover them all.
USAGE_RUNS_LISTED = 200


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


@router.get(
    "/{conversation_id}/usage",
    response_model=ConversationUsageResponse,
    operation_id="agent.conversation.usage.get",
    dependencies=[CONVERSATION_MEMBERSHIP],
    summary="Get Conversation Usage",
    description=(
        "What a conversation has cost so far, in US dollars and model tokens: "
        "summed over every run, and run by run for the newest "
        f"{USAGE_RUNS_LISTED}. Readable by whoever may read the conversation "
        "(its owner), and only from what was recorded against them: the same "
        "rows their own usage shows. A run with nothing recorded has null "
        "`usage` and is counted in `unreported_run_count`: an Agent Host run "
        "whose harness reports no usage, or one not reported yet. Sub-agent "
        "conversations are not included; read each one's own."
    ),
)
async def get_conversation_usage(
    pod_id: UUID,
    conversation_id: UUID,
    user: CurrentUser,
    reads: ConversationReadsDep,
) -> ConversationUsageResponse:
    ledger = await reads.get_usage(
        conversation_id=conversation_id,
        user_id=user.id,
        pod_id=pod_id,
        run_limit=USAGE_RUNS_LISTED,
    )
    return ConversationUsageResponse.of(conversation_id, ledger)
