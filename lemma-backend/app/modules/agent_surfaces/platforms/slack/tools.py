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
from app.modules.agent_surfaces.platforms.tool_guard import guarded_tool_result


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
        return await guarded_tool_result(
            service.get_recent_channel_messages(ctx=ctx, request=request),
            tool="slack_get_recent_channel_messages",
            failure=SlackRecentChannelMessagesResult(
                success=False,
                error="Slack channel history lookup failed unexpectedly.",
            ),
        )

    async def slack_search_current_channel(
        ctx: RunContext[ConversationContext],
        request: SlackSearchChannelMessagesParams,
    ) -> SlackSearchChannelMessagesResult:
        """Search recent messages in the current Slack channel without leaving the active agent conversation."""
        return await guarded_tool_result(
            service.search_current_channel(ctx=ctx, request=request),
            tool="slack_search_current_channel",
            failure=SlackSearchChannelMessagesResult(
                success=False,
                error="Slack channel search failed unexpectedly.",
            ),
        )

    return FunctionToolset[ConversationContext](
        tools=[
            slack_get_recent_channel_messages,
            slack_search_current_channel,
        ]
    )
