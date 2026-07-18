from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.platforms.teams.models import (
    TeamsGetRecentMessagesParams,
    TeamsGetRecentMessagesResult,
)
from app.modules.agent_surfaces.platforms.teams.service import TeamsPlatformService
from app.core.log.log import get_logger

logger = get_logger(__name__)


def build_teams_surface_toolset(
    *,
    credentials: dict[str, Any],
) -> FunctionToolset[ConversationContext]:
    service = TeamsPlatformService(credentials=credentials)

    async def teams_get_recent_channel_messages(
        ctx: RunContext[ConversationContext],
        request: TeamsGetRecentMessagesParams,
    ) -> TeamsGetRecentMessagesResult:
        """Get recent messages from the current Teams channel or thread, including shared files."""
        try:
            return await service.get_recent_channel_messages(ctx=ctx, request=request)
        except Exception:
            logger.debug(
                "surface.teams.history_failed", exc_info=True
            )
            return TeamsGetRecentMessagesResult(
                success=False,
                error="Teams channel history lookup failed unexpectedly.",
            )

    return FunctionToolset[ConversationContext](
        tools=[
            teams_get_recent_channel_messages,
        ]
    )
