"""``surface.platform_update``: a webhook about the app or a group, not a message.

The bot joined a channel, somebody opened the app home, WhatsApp created (or
refused) a group the bot asked for, a send Meta now says failed. Each used to
be applied inline in the one serial consumer every pod's messages waited
behind; now each is its own small item, written by the request that received
it. A JOB: an app home is published back to Slack, a group's invite link is
fetched from Meta.
"""

from __future__ import annotations

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.work import (
    JobContext,
    RetryPolicy,
    WorkClass,
    WorkMode,
    WorkRegistry,
    registry as default_registry,
)
from app.modules.agent_surfaces.domain.work_payloads import PlatformUpdatePayload
from app.modules.agent_surfaces.events.work_kinds import SURFACE_PLATFORM_UPDATE
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)


async def apply_platform_update(
    ctx: JobContext, payload: PlatformUpdatePayload
) -> None:
    from app.modules.agent_surfaces.composition import build_app_event_handler
    from app.modules.agent_surfaces.services.group_updates import apply_group_updates
    from app.modules.agent_surfaces.services.whatsapp_delivery_status import (
        apply_delivery_statuses,
    )

    request = payload.request
    adapters = SurfacePlatformAdapterRegistry()
    async with SessionUnitOfWorkFactory(async_session_maker)() as uow:
        if await build_app_event_handler(uow).try_handle_lifecycle(request):
            return
        await apply_group_updates(uow, request, adapters=adapters)
        await apply_delivery_statuses(uow, request)


def register_platform_update_work(registry: WorkRegistry) -> None:
    registry.handler(
        kind=SURFACE_PLATFORM_UPDATE,
        cls=WorkClass.EVENTS,
        mode=WorkMode.JOB,
        payload=PlatformUpdatePayload,
        retry=RetryPolicy(max_attempts=3),
    )(apply_platform_update)


register_platform_update_work(default_registry)
