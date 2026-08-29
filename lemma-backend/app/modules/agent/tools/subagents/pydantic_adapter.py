"""Sub-agent control toolset: spawn and interact with running sub-conversations.

Lets an agent launch other pod agents as real, linked child conversations and
then poll, message, or stop them — instead of only blocking inline calls.

The surface is three tools: ``spawn_subagent`` launches a child,
``interact_subagent`` drives a running child (send/await/stop), and
``query_subagents`` inspects children (list/messages).
"""

from __future__ import annotations

from uuid import UUID

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.tools.tool_payload_limits import bounded_tool_payload
from app.modules.agent.tools.tool_errors import safe_error_text
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.services.serialization import message_to_payload
from app.modules.agent.services.subagent_service import SubAgentError, SubAgentService
from app.modules.agent.tools.context import BaseAgentContext
from app.modules.agent.tools.subagents.models import (
    InteractSubagentRequest,
    QuerySubagentsRequest,
    SpawnSubagentRequest,
)


def _service() -> SubAgentService:
    return SubAgentService(SessionUnitOfWorkFactory(async_session_maker))


def _handle_payload(handle) -> JsonObject:
    return {
        "success": True,
        "conversation_id": str(handle.conversation_id),
        "run_id": str(handle.run_id) if handle.run_id else None,
        "status": handle.status,
    }


async def spawn_subagent(
    ctx: RunContext[BaseAgentContext],
    request: SpawnSubagentRequest,
) -> JsonObject:
    """Start a pod agent as a linked child conversation; returns a handle.

    Omit agent_name to spawn another instance of yourself for a targeted subtask.
    Non-blocking: returns {conversation_id, run_id, status}. Use interact_subagent
    (action='await') to wait, or query_subagents (mode='messages') to poll.
    """
    try:
        handle = await _service().spawn(
            ctx.deps,
            agent_name=request.agent_name,
            input_data=request.input,
        )
    except SubAgentError as exc:
        return {"success": False, "error": safe_error_text(exc)}
    return _handle_payload(handle)


async def interact_subagent(
    ctx: RunContext[BaseAgentContext],
    request: InteractSubagentRequest,
) -> JsonObject:
    """Drive a running sub-agent you spawned.

    action='send'  → post a follow-up message (starts a new turn); needs content.
    action='await' → block until a run finishes (bounded by timeout_seconds); needs run_id.
    action='stop'  → request a graceful stop.
    """
    try:
        conversation_id = UUID(request.conversation_id)
        if request.action == "send":
            if not request.content:
                return {
                    "success": False,
                    "error": "content is required when action='send'.",
                }
            handle = await _service().send(
                ctx.deps,
                conversation_id=conversation_id,
                content=request.content,
            )
            return _handle_payload(handle)
        if request.action == "await":
            if not request.run_id:
                return {
                    "success": False,
                    "error": "run_id is required when action='await'.",
                }
            # A child run's whole output, which carries its own tool results.
            awaited = await _service().await_run(
                ctx.deps,
                conversation_id=conversation_id,
                run_id=UUID(request.run_id),
                timeout_seconds=request.timeout_seconds,
            )
            return {
                "success": True,
                **{
                    key: bounded_tool_payload(value, what="sub-agent output")
                    for key, value in awaited.items()
                },
            }
        # action == "stop"
        return {
            "success": True,
            **await _service().stop(ctx.deps, conversation_id=conversation_id),
        }
    except SubAgentError as exc:
        return {"success": False, "error": safe_error_text(exc)}


async def query_subagents(
    ctx: RunContext[BaseAgentContext],
    request: QuerySubagentsRequest,
) -> JsonObject:
    """Inspect the sub-agents you spawned.

    mode='list'     → child conversations with latest run status (status='ACTIVE'
                      filters to running ones).
    mode='messages' → latest messages from one child; needs conversation_id.
    """
    try:
        if request.mode == "messages":
            if not request.conversation_id:
                return {
                    "success": False,
                    "error": "conversation_id is required when mode='messages'.",
                }
            messages = await _service().get_messages(
                ctx.deps,
                conversation_id=UUID(request.conversation_id),
                after_sequence=request.after_sequence,
                limit=request.limit,
            )
            # A child run's transcript, its own large tool results included.
            return {
                "success": True,
                "messages": bounded_tool_payload(
                    [message_to_payload(message) for message in messages],
                    what="sub-agent transcript",
                ),
            }
        # mode == "list"
        children = await _service().list_children(
            ctx.deps,
            limit=request.limit,
            status_filter=request.status,
        )
        return {"success": True, "children": children}
    except SubAgentError as exc:
        return {"success": False, "error": safe_error_text(exc)}


subagents_toolset = FunctionToolset[BaseAgentContext](
    tools=[
        spawn_subagent,
        interact_subagent,
        query_subagents,
    ]
)
