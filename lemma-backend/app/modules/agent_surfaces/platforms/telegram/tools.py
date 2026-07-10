from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.platforms.telegram.models import (
    TelegramCurrentChatParams,
    TelegramCurrentChatResult,
)
from app.modules.agent_surfaces.platforms.telegram.service import (
    TelegramPlatformService,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


def build_telegram_surface_toolset(
    *,
    credentials: dict[str, Any],
) -> FunctionToolset[ConversationContext]:
    service = TelegramPlatformService(credentials=credentials)

    async def telegram_get_current_chat(
        ctx: RunContext[ConversationContext],
        request: TelegramCurrentChatParams,
    ) -> TelegramCurrentChatResult:
        """Get the current Telegram chat and topic details for this conversation."""
        try:
            return await service.get_current_chat(ctx=ctx, request=request)
        except Exception as exc:
            logger.exception("Telegram tool telegram_get_current_chat failed: %s", exc)
            return TelegramCurrentChatResult(
                success=False,
                error="Could not load current Telegram chat details.",
            )

    return FunctionToolset[ConversationContext](
        tools=[
            telegram_get_current_chat,
        ]
    )
