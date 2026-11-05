"""Sub-agent control toolset: spawn and interact with running sub-conversations.

Lets an agent launch other pod agents as real, linked child conversations and
then poll, message, or stop them — instead of only blocking inline calls.

``delegate`` (``subagents/delegate``) is the one an agent sees: it hands out
several tasks and waits once. The three here sit behind tool search:
``interact_subagent`` drives a running child (send/await/stop),
``query_subagents`` inspects children (list/messages), and ``spawn_subagent``
-- deprecated, kept because a harness over MCP may still call it -- starts one
child the way ``delegate(wait="none")`` does.
"""

from __future__ import annotations

from uuid import UUID


from app.modules.agent.infrastructure.tools.payload_limits import bounded_tool_payload
from app.modules.agent.infrastructure.tools.tool_errors import safe_error_text
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.domain.live_events import message_to_payload
from app.modules.agent.services.subagent_service import SubAgentError, SubAgentService
from app.modules.agent.domain.context import ToolContext
from collections.abc import Mapping

from app.modules.agent.domain.replay import request_body
from app.modules.agent.domain.tools import ReplayClass, ToolGroup, ToolPolicy, tool
from app.modules.agent.infrastructure.tools.subagents.delegate import (
    SPECS as delegate_specs,
)
from app.modules.agent.infrastructure.tools.subagents.models import (
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
    ctx: ToolContext,
    request: SpawnSubagentRequest,
) -> JsonObject:
    """Deprecated: use `delegate`. Start one child; returns a handle.

    Non-blocking: returns {conversation_id, run_id, status}. To collect the
    result, call `wait_for` with `subagent_run_id` and end your turn; you wake
    with its report. If you end your turn without waiting, it arrives later.
    """
    try:
        handle = await _service().spawn(
            ctx,
            agent_name=request.agent_name,
            input_data=request.input,
            # The recorded call id: a resumed turn re-runs this call under it
            # and gets the child it already spawned (KEYED, `domain/replay`).
            tool_call_id=ctx.tool_call_id,
        )
    except SubAgentError as exc:
        return {"success": False, "error": safe_error_text(exc)}
    return _handle_payload(handle)


async def interact_subagent(
    ctx: ToolContext,
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
                ctx,
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
                ctx,
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
            **await _service().stop(ctx, conversation_id=conversation_id),
        }
    except SubAgentError as exc:
        return {"success": False, "error": safe_error_text(exc)}


async def query_subagents(
    ctx: ToolContext,
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
                ctx,
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
            ctx,
            limit=request.limit,
            status_filter=request.status,
        )
        return {"success": True, "children": children}
    except SubAgentError as exc:
        return {"success": False, "error": safe_error_text(exc)}


def _interaction_replay(args: Mapping[str, object]) -> ReplayClass:
    """Awaiting or stopping a child is safe twice; sending it a message is not."""
    action = str(request_body(args).get("action") or "").lower()
    return ReplayClass.SAFE if action in {"await", "stop"} else ReplayClass.UNSAFE


SPECS = (
    *delegate_specs,
    # A spawn is unique on (parent run, call).
    tool(
        spawn_subagent,
        ToolGroup.SUBAGENTS,
        ToolPolicy(deferrable=True, replay=ReplayClass.KEYED),
    ),
    tool(
        interact_subagent,
        ToolGroup.SUBAGENTS,
        ToolPolicy(deferrable=True, replay=_interaction_replay),
    ),
    tool(
        query_subagents,
        ToolGroup.SUBAGENTS,
        ToolPolicy(deferrable=True, replay=ReplayClass.SAFE),
    ),
)
