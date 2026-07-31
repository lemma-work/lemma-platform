from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.platforms.email_models import (
    OutlookReplyEmailParams,
    OutlookReplyEmailResult,
)
from app.modules.agent_surfaces.platforms.outlook.service import (
    OutlookPlatformService,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


def build_outlook_surface_toolset(
    *,
    credentials: dict[str, Any],
) -> FunctionToolset[ConversationContext]:
    service = OutlookPlatformService(credentials=credentials)

    async def outlook_reply_email(
        ctx: RunContext[ConversationContext],
        request: OutlookReplyEmailParams,
    ) -> OutlookReplyEmailResult:
        """Reply to the current Outlook thread with formatted content and optional pod-file attachments."""
        try:
            return await service.reply_email(ctx=ctx, request=request)
        except Exception:
            logger.debug(
                "surface.email.reply_failed",
                exc_info=True,
            )
            return OutlookReplyEmailResult(
                success=False,
                error="Outlook reply failed unexpectedly.",
            )

    return FunctionToolset[ConversationContext](
        tools=[
            outlook_reply_email,
        ]
    )
