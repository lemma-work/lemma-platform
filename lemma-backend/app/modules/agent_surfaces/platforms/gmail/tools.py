from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.platforms.email_models import (
    GmailReplyEmailParams,
    GmailReplyEmailResult,
)
from app.modules.agent_surfaces.platforms.gmail.service import GmailPlatformService
from app.core.log.log import get_logger

logger = get_logger(__name__)


def build_gmail_surface_toolset(
    *,
    credentials: dict[str, Any],
) -> FunctionToolset[ConversationContext]:
    service = GmailPlatformService(credentials=credentials)

    async def gmail_reply_email(
        ctx: RunContext[ConversationContext],
        request: GmailReplyEmailParams,
    ) -> GmailReplyEmailResult:
        """Reply to the current Gmail thread with formatted content and optional pod-file attachments."""
        try:
            return await service.reply_email(ctx=ctx, request=request)
        except Exception as exc:
            logger.exception("Gmail tool gmail_reply_email failed: %s", exc)
            return GmailReplyEmailResult(
                success=False,
                error="Gmail reply failed unexpectedly.",
            )

    return FunctionToolset[ConversationContext](
        tools=[
            gmail_reply_email,
        ]
    )
