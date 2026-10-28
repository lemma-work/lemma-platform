"""The bot's end of a member proving their Telegram account.

``services/telegram_identity_link`` mints a code in the member's signed-in
Lemma session; they open it in Telegram, and the bot hears ``/start lk_<code>``
in a private chat. This recognises that message before anything treats it as
a question -- before onboarding, before routing -- records which account
opened it, and answers that account, without naming the member: the member
confirms in Lemma.

Synchronous, like a group's ``/start`` (``telegram_group_join``): it is a
Redis read and a Telegram send, and neither should wait for a worker.
"""

from __future__ import annotations

from uuid import UUID

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfaceIngressRequest,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.telegram_group_join import (
    telegram_adapter_for,
)
from app.modules.agent_surfaces.services.telegram_identity_link import (
    EXPIRED_REPLY,
    is_shared_bot_surface,
    open_link,
    peek_link,
    start_code,
)

logger = get_logger(__name__)


async def claim_telegram_identity_link(
    uow_factory: UnitOfWorkFactory,
    request: SurfaceIngressRequest,
    *,
    adapters: SurfacePlatformAdapterRegistry,
    redis=None,
) -> bool:
    """Handle a private chat's ``/start lk_<code>``. True when this was one."""
    telegram = telegram_adapter_for(request, adapters)
    if telegram is None:
        return False
    parsed = await telegram.parse_inbound_event(request.payload, request.headers)
    if parsed is None or not parsed.is_dm:
        return False
    secret = start_code(parsed.message_text)
    if secret is None:
        return False
    received_by = _receivers(request)
    code = await peek_link(secret, redis=redis)
    # Answered through the bot the code names, or, for a code nobody knows,
    # the bot that heard it -- when the delivery says which one that is.
    surface_id = code.surface_id if code is not None else _one(received_by)
    if surface_id is None:
        return True
    async with uow_factory() as uow:
        surface = await SurfaceRepository(uow).get(surface_id)
        if surface is None:
            return True
        credentials = await SurfaceCredentialResolver(uow=uow).for_surface(surface)
    reply = (
        await open_link(
            secret,
            parsed=parsed,
            received_by=received_by,
            shared_bot_surface=is_shared_bot_surface(surface),
            redis=redis,
        )
        if code is not None
        else EXPIRED_REPLY
    )
    try:
        await telegram.send_message(
            credentials=credentials, event=parsed, message=reply
        )
    except platform_transport_errors():
        logger.info(
            "agent_surfaces.telegram_identity_link.reply_failed.observed",
            surface_id=str(surface_id),
            exc_info=True,
        )
    return True


def _receivers(request: SurfaceIngressRequest) -> list[UUID] | None:
    """The surfaces the delivering bot serves; ``None`` for a platform-wide
    delivery, which only the shared bot sends."""
    if isinstance(request, SurfaceDirectWebhookIngress):
        return [request.surface_id]
    return request.receiver_surface_ids


def _one(surface_ids: list[UUID] | None) -> UUID | None:
    return surface_ids[0] if surface_ids else None
