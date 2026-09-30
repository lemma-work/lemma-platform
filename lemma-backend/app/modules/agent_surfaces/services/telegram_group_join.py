"""The moment a pod's Telegram bot is added to a group through a Lemma link.

``services/telegram_group_links`` mints the link; Telegram adds the bot and
sends ``/start@<bot> <code>`` in the group. This recognises that message before
anything treats it as a question, spends the code, and makes the group the
pod's: registered, with the member who asked for the link answering for its
people outside the pod, and the bot saying hello so the group knows it is
there.

The code makes the member the group's owner and nothing more. It does not
make the Telegram account that sent it theirs: a link is easy to pass on --
"add our bot to your group" -- and linking whoever used it would hand that
person the member's own access in a private chat with the bot. Who a Telegram
account belongs to stays the question it always was, answered by the
member's profile.

Every other ``/start`` in a group -- an unknown code, a spent one, somebody
else's bot -- is swallowed here too, so a stale link is never mistaken for a
question put to the bot.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.adapter_port import (
    SurfacePlatformAdapterPort,
)
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
    SurfacePlatform,
    platform_value_for_source,
)
from app.modules.agent_surfaces.domain.ingress_request import (
    SurfaceDirectWebhookIngress,
    SurfaceIngressRequest,
)
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.infrastructure.repositories.group_repository import (
    SurfaceGroupRepository,
)
from app.modules.agent_surfaces.infrastructure.repositories.surface_repository import (
    SurfaceRepository,
)
from app.modules.agent_surfaces.platforms.common import PLATFORM_TRANSPORT_ERRORS
from app.modules.agent_surfaces.services.credential_resolver import (
    SurfaceCredentialResolver,
)
from app.modules.agent_surfaces.services.group_names import visible_bot_name
from app.modules.agent_surfaces.services.pod_name_lookup import pod_name_for
from app.modules.agent_surfaces.services.telegram_group_links import (
    GroupLinkClaim,
    redeem_group_link,
    start_code,
)

logger = get_logger(__name__)


async def claim_telegram_group_join(
    uow_factory: UnitOfWorkFactory,
    request: SurfaceIngressRequest,
    *,
    adapters: SurfacePlatformAdapterRegistry,
    redis=None,
) -> bool:
    """Handle a group's ``/start <code>``. True when this message was one.

    Called before any session is open: spending the code is a Redis call and
    the hello a Telegram one, and neither should hold a connection.
    """
    telegram = _telegram_adapter(request, adapters)
    if telegram is None:
        return False
    parsed = await telegram.parse_inbound_event(request.payload, request.headers)
    if parsed is None or parsed.is_dm:
        return False
    code = start_code(parsed.message_text)
    if code is None:
        return False
    claim = await redeem_group_link(code, redis=redis)
    if claim is None or not parsed.external_channel_id:
        return True
    async with uow_factory() as uow:
        adopted = await _adopt(uow, claim=claim, parsed=parsed)
    if adopted is not None:
        await _say_hello(telegram, adopted, parsed=parsed)
    return True


def _telegram_adapter(
    request: SurfaceIngressRequest, adapters: SurfacePlatformAdapterRegistry
) -> SurfacePlatformAdapterPort | None:
    """The Telegram adapter, when this delivery could be a Telegram update."""
    if isinstance(request, SurfaceDirectWebhookIngress):
        # A bot's own webhook names its surface, not its platform; a Telegram
        # update is recognisable by shape, and the code names the surface.
        is_telegram = "update_id" in request.payload
    else:
        is_telegram = (
            platform_value_for_source(request.source) == SurfacePlatform.TELEGRAM.value
        )
    return adapters.get(SurfacePlatform.TELEGRAM.value) if is_telegram else None


@dataclass(frozen=True, slots=True)
class _Adopted:
    surface: AgentSurfaceEntity
    credentials: dict[str, object]
    name: str
    pod: str


async def _adopt(
    uow: SqlAlchemyUnitOfWork,
    *,
    claim: GroupLinkClaim,
    parsed: ParsedInboundSurfaceEvent,
) -> _Adopted | None:
    """Make the group the pod's, answered for by the member the link was for."""
    surface = await SurfaceRepository(uow).get(claim.surface_id)
    if surface is None or surface.surface_type is not SurfacePlatform.TELEGRAM:
        return None
    groups = SurfaceGroupRepository(uow.session)
    group = await groups.ensure(
        pod_id=surface.pod_id,
        surface_id=surface.id,
        platform=SurfacePlatform.TELEGRAM.value,
        external_channel_id=str(parsed.external_channel_id),
        title=_title(parsed),
    )
    if group.owner_user_id is None:
        await groups.set_owner(group.id, claim.user_id)
    name = await visible_bot_name(uow, surface)
    pod = await pod_name_for(uow, surface.pod_id) or name
    credentials = await SurfaceCredentialResolver(uow=uow).for_surface(surface)
    await uow.commit()
    logger.info(
        "agent_surfaces.telegram_group_join.adopted.observed",
        group_id=str(group.id),
        surface_id=str(surface.id),
    )
    return _Adopted(surface=surface, credentials=credentials, name=name, pod=pod)


def _title(parsed: ParsedInboundSurfaceEvent) -> str | None:
    title = parsed.metadata.get("chat_title")
    return title if isinstance(title, str) and title.strip() else None


async def _say_hello(
    telegram: SurfacePlatformAdapterPort,
    adopted: _Adopted,
    *,
    parsed: ParsedInboundSurfaceEvent,
) -> None:
    """Tell the group the bot is there and how to ask it. Best-effort."""
    message = (
        f"Hi, I'm {adopted.name}. Mention me or reply to me to ask something. "
        f"People outside {adopted.pod} get answers from what {adopted.pod} "
        "has made public."
    )
    try:
        await telegram.send_message(
            credentials=adopted.credentials, event=parsed, message=message
        )
    except PLATFORM_TRANSPORT_ERRORS:
        logger.info(
            "agent_surfaces.telegram_group_join.hello_failed.observed",
            surface_id=str(adopted.surface.id),
            exc_info=True,
        )
