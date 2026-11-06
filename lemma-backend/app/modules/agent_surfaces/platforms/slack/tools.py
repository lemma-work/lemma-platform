from __future__ import annotations

from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials


from app.modules.agent.contracts.tools import ToolContext, ToolGroup, ToolSpec, tool
from app.modules.agent_surfaces.platforms.slack.models import (
    SlackRecentChannelMessagesParams,
    SlackRecentChannelMessagesResult,
    SlackSearchChannelMessagesParams,
    SlackSearchChannelMessagesResult,
)
from app.modules.agent_surfaces.platforms.slack.service import SlackPlatformService
from app.modules.agent_surfaces.platforms.tool_guard import guarded_tool_result


def slack_surface_tools(
    *,
    credentials: SurfaceCredentials,
) -> tuple[ToolSpec, ...]:
    service = SlackPlatformService(credentials=credentials)

    async def slack_get_recent_channel_messages(
        ctx: ToolContext,
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
        ctx: ToolContext,
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

    return (
        tool(slack_get_recent_channel_messages, ToolGroup.SURFACE),
        tool(slack_search_current_channel, ToolGroup.SURFACE),
    )
