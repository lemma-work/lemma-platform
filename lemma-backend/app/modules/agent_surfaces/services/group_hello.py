"""What the bot says about itself when it arrives in a group.

Everybody in a group learns three things the moment the bot is there, strangers
included: how to ask it; that people outside the pod are answered from what the
pod made Public; and that the pod keeps what is said there, and for how long. A
group's members never agreed to a log, so they are told it exists before
anything goes into it.

Said once: as a message when the bot is added to a Telegram group, however it
was added, and as the description of a WhatsApp group the bot creates.
"""

from __future__ import annotations

from typing import Any

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ConversationType,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.groups import GROUP_LOG_RETENTION
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.services.group_names import visible_bot_name
from app.modules.agent_surfaces.services.pod_name_lookup import pod_name_for

logger = get_logger(__name__)


def group_hello(*, name: str, pod: str) -> str:
    return (
        f"Hi, I'm {name}. Mention me or reply to me to ask something. "
        f"People outside {pod} get answers from what {pod} has made public. "
        f"{pod} keeps what's said here for {GROUP_LOG_RETENTION.days} days, "
        "so I can follow along."
    )


async def hello_for(uow: SqlAlchemyUnitOfWork, surface: AgentSurfaceEntity) -> str:
    """The hello this surface's bot says, under the name the app shows it by."""
    name = await visible_bot_name(uow, surface)
    return group_hello(name=name, pod=await pod_name_for(uow, surface.pod_id) or name)


async def say_hello_in_group(
    uow: SqlAlchemyUnitOfWork,
    *,
    surface: AgentSurfaceEntity,
    adapter: SurfacePlatformAdapterPort,
    credentials: dict[str, Any],
    channel_id: str,
) -> None:
    """Post the hello into a group the bot was just added to. Best-effort."""
    message = await hello_for(uow, surface)
    event = ParsedInboundSurfaceEvent(
        platform=surface.surface_type,
        conversation_type=ConversationType.EXTERNAL_GROUP,
        external_channel_id=channel_id,
        external_thread_id=channel_id,
        message_text="",
        is_dm=False,
        reply_target={"chat_id": channel_id},
    )
    try:
        async with connection_released(uow.session):
            await adapter.send_message(
                credentials=credentials, event=event, message=message
            )
    except PLATFORM_TRANSPORT_ERRORS:
        logger.info(
            "agent_surfaces.group_hello.send_failed.observed",
            surface_id=str(surface.id),
            exc_info=True,
        )
