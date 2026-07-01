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

from app.core.log.log import get_logger
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent_surfaces.services.surface_display_delivery import (
    deliver_surface_message_to_surface,
)

logger = get_logger(__name__)


class SurfaceSendMessageResult(BaseModel):
    success: bool
    message: str | None = Field(
        default=None, description="Error detail when delivery failed."
    )


def build_surface_send_toolset() -> FunctionToolset[ConversationContext]:
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
        try:
            sent = await deliver_surface_message_to_surface(
                conversation_id=conversation_id, message=message
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception("surface_send_message failed: %s", exc)
            return SurfaceSendMessageResult(
                success=False, message="Could not deliver the message."
            )
        return SurfaceSendMessageResult(
            success=sent,
            message=None if sent else "No reachable surface for this conversation.",
        )

    return FunctionToolset[ConversationContext](tools=[surface_send_message])
