from __future__ import annotations

from typing import Any

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.contracts import ConversationContext
from app.modules.agent_surfaces.platforms.whatsapp.models import (
    WhatsAppCurrentContactParams,
    WhatsAppCurrentContactResult,
)
from app.modules.agent_surfaces.platforms.whatsapp.service import (
    WhatsAppPlatformService,
)
from app.core.log.log import get_logger

logger = get_logger(__name__)


def build_whatsapp_surface_toolset(
    *,
    credentials: dict[str, Any],
) -> FunctionToolset[ConversationContext]:
    service = WhatsAppPlatformService(credentials=credentials)

    async def whatsapp_get_current_contact(
        ctx: RunContext[ConversationContext],
        request: WhatsAppCurrentContactParams,
    ) -> WhatsAppCurrentContactResult:
        """Get the current WhatsApp contact and destination details for this conversation."""
        try:
            return await service.get_current_contact(ctx=ctx, request=request)
        except Exception as exc:
            logger.exception("WhatsApp tool whatsapp_get_current_contact failed: %s", exc)
            return WhatsAppCurrentContactResult(
                success=False,
                error="Could not load current WhatsApp contact details.",
            )

    return FunctionToolset[ConversationContext](
        tools=[
            whatsapp_get_current_contact,
        ]
    )
