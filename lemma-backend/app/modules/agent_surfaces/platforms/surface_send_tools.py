"""The current-user ``surface_send_message`` agent tool.

A generic, platform-neutral tool that lets an agent proactively send a message
to the person it is working for on the current surface (instead of waiting for
its final reply). It only ever reaches the current conversation's user; reaching
other pod members is the ``surface.send`` API, not this tool.
"""

from __future__ import annotations

from pydantic import BaseModel, Field
from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.platforms.tool_guard import guarded_tool_result
from app.modules.agent_surfaces.services.surface_display_delivery import (
    deliver_surface_message_to_surface,
)


class SurfaceSendMessageResult(BaseModel):
    success: bool
    message: str | None = Field(
        default=None, description="Error detail when delivery failed."
    )


def build_surface_send_toolset(
    deliver=deliver_surface_message_to_surface,
) -> FunctionToolset[ConversationContext]:
    async def surface_send_message(
        ctx: RunContext[ConversationContext],
        message: str,
    ) -> SurfaceSendMessageResult:
        """Send a message to the current user on this surface right now.

        Use to reach the person you're working for mid-task (a heads-up, an
        interim result) rather than waiting for your final reply. Delivered to
        the current conversation's user only.
        """
        conversation_id = getattr(ctx.deps, "conversation_id", None)
        if conversation_id is None:
            return SurfaceSendMessageResult(
                success=False, message="No active surface conversation."
            )
        if not getattr(ctx.deps, "delivers_to_surface", True):
            # A private note's run answers in Lemma only -- the same rule
            # `display_resource` and the progress observer keep.
            return SurfaceSendMessageResult(
                success=False,
                message="This was a private note: its answer stays in Lemma.",
            )
        sent = await guarded_tool_result(
            deliver(conversation_id=conversation_id, message=message),
            tool="surface_send_message",
            failure=None,
        )
        if sent is None:
            return SurfaceSendMessageResult(
                success=False, message="Could not deliver the message."
            )
        return SurfaceSendMessageResult(
            success=sent,
            message=None if sent else "No reachable surface for this conversation.",
        )

    return FunctionToolset[ConversationContext](tools=[surface_send_message])
