from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.platforms.slack.models import (
    SlackRecentChannelMessagesParams,
    SlackRecentChannelMessagesResult,
    SlackSearchChannelMessagesParams,
    SlackSearchChannelMessagesResult,
)
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService
from app.core.log.log import get_logger

logger = get_logger(__name__)


def build_slack_surface_toolset(
    *,
    credentials: dict[str, Any],
) -> FunctionToolset[ConversationContext]:
    service = SlackPlatformService(credentials=credentials)

    async def slack_get_recent_channel_messages(
        ctx: RunContext[ConversationContext],
        request: SlackRecentChannelMessagesParams,
    ) -> SlackRecentChannelMessagesResult:
        """Get recent messages from the current Slack channel, including any shared files."""
        try:
            return await service.get_recent_channel_messages(ctx=ctx, request=request)
        except Exception:
            logger.debug(
                "surface.slack.history_failed", exc_info=True
            )
            return SlackRecentChannelMessagesResult(
                success=False,
                error="Slack channel history lookup failed unexpectedly.",
            )

    async def slack_search_current_channel(
        ctx: RunContext[ConversationContext],
        request: SlackSearchChannelMessagesParams,
    ) -> SlackSearchChannelMessagesResult:
        """Search recent messages in the current Slack channel without leaving the active agent conversation."""
        try:
            return await service.search_current_channel(ctx=ctx, request=request)
        except Exception:
            logger.debug(
                "surface.slack.search_failed",
                exc_info=True,
            )
            return SlackSearchChannelMessagesResult(
                success=False,
                error="Slack channel search failed unexpectedly.",
            )

    return FunctionToolset[ConversationContext](
        tools=[
            slack_get_recent_channel_messages,
            slack_search_current_channel,
        ]
    )
