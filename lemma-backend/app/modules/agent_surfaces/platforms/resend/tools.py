from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.core.log.log import get_logger
from app.modules.agent.tools.context import ConversationContext
from app.modules.agent_surfaces.platforms.email_models import (
    ResendReplyEmailParams,
    ResendReplyEmailResult,
)
from app.modules.agent_surfaces.platforms.resend.service import ResendPlatformService

logger = get_logger(__name__)


def build_resend_surface_toolset(
    *,
    credentials: dict[str, Any],
) -> FunctionToolset[ConversationContext]:
    service = ResendPlatformService(credentials=credentials)

    async def resend_reply_email(
        ctx: RunContext[ConversationContext],
        request: ResendReplyEmailParams,
    ) -> ResendReplyEmailResult:
        """Reply to the current email thread with formatted content and optional pod-file attachments."""
        try:
            return await service.reply_email(ctx=ctx, request=request)
        except Exception as exc:
            logger.exception("Resend tool resend_reply_email failed: %s", exc)
            return ResendReplyEmailResult(
                success=False,
                error="Email reply failed unexpectedly.",
            )

    return FunctionToolset[ConversationContext](tools=[resend_reply_email])
