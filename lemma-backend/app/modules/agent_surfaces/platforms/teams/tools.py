from __future__ import annotations

from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials


from app.modules.agent.contracts.tools import ToolContext, ToolGroup, ToolSpec, tool
from app.modules.agent_surfaces.platforms.teams.models import (
    TeamsGetRecentMessagesParams,
    TeamsGetRecentMessagesResult,
)
from app.modules.agent_surfaces.platforms.teams.service import TeamsPlatformService
from app.modules.agent_surfaces.platforms.tool_guard import guarded_tool_result


def teams_surface_tools(
    *,
    credentials: SurfaceCredentials,
) -> tuple[ToolSpec, ...]:
    service = TeamsPlatformService(credentials=credentials)

    async def teams_get_recent_channel_messages(
        ctx: ToolContext,
        request: TeamsGetRecentMessagesParams,
    ) -> TeamsGetRecentMessagesResult:
        """Get recent messages from the current Teams channel or thread, including shared files."""
        return await guarded_tool_result(
            service.get_recent_channel_messages(ctx=ctx, request=request),
            tool="teams_get_recent_channel_messages",
            failure=TeamsGetRecentMessagesResult(
                success=False,
                error="Teams channel history lookup failed unexpectedly.",
            ),
        )

    return (tool(teams_get_recent_channel_messages, ToolGroup.SURFACE),)
