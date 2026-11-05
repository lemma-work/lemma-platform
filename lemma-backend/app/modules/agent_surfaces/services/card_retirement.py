"""Taking the buttons off cards whose pause is over.

A person shown an approval who types something else instead has moved on, and
the turn their message starts denies the pause for them. The card stayed on
screen with live buttons, so a later tap looked like it could still approve --
it was answered "already cancelled", but only after it had been tried. Retiring
the card when the turn starts is what makes the screen match the decision.

The same for a pause answered by a typed reply, or in the web app: the answer
is in, and the card's buttons would only offer to answer it again.

After the commit, never inside it: the denial is only real once the turn that
records it has committed, and a platform edit is a network call nobody should
hold a connection across.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import UUID

from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.adapter_port import SurfacePlatformAdapterPort
from app.modules.agent_surfaces.domain.credentials import SurfaceCredentials
from app.modules.agent_surfaces.domain.decision_card import (
    CardRetirement,
    DecisionCardHandle,
)
from app.modules.agent_surfaces.platforms.common import platform_transport_errors
from app.modules.agent_surfaces.platforms.decision_cards import take_decision_cards
from app.modules.agent_surfaces.services.display_resource_renderer import (
    build_callback_id,
)

if TYPE_CHECKING:
    from app.modules.agent_surfaces.services.egress_service import SurfaceEgress

logger = get_logger(__name__)


async def retire_superseded_cards(
    *,
    adapter: SurfacePlatformAdapterPort,
    credentials: SurfaceCredentials,
    conversation_id: UUID,
    tool_call_ids: list[str],
    reason: CardRetirement = CardRetirement.SUPERSEDED,
) -> None:
    """Retire every card of each pause that has any. Never raises."""
    for tool_call_id in tool_call_ids:
        for handle in await take_decision_cards(
            build_callback_id(conversation_id, tool_call_id)
        ):
            await _retire(adapter, credentials, conversation_id, handle, reason)


async def retire_cards_answered_in_lemma(
    *,
    uow_factory: UnitOfWorkFactory,
    egress_factory: Callable[[SqlAlchemyUnitOfWork], SurfaceEgress],
    conversation_id: UUID,
    tool_call_ids: list[str],
) -> None:
    """Retire the cards of pauses answered in the web app. Never raises.

    A tap retires its own card as it is answered, and a typed reply retires
    the cards when its turn starts; an answer given in Lemma goes through
    neither, so the cards it answered kept their buttons. The target is read in
    a scope of its own and released before any platform is called.
    """
    try:
        async with uow_factory() as uow:
            target = await egress_factory(uow).delivery.resolve_egress_target(
                conversation_id
            )
    except SQLAlchemyError, RedisError:
        logger.warning(
            "agent_surfaces.card_retirement.target_unavailable.degraded",
            conversation_id=str(conversation_id),
            exc_info=True,
        )
        return
    if target is None:
        return
    await retire_superseded_cards(
        adapter=target.adapter,
        credentials=target.credentials,
        conversation_id=conversation_id,
        tool_call_ids=tool_call_ids,
        reason=CardRetirement.ANSWERED,
    )


async def _retire(
    adapter: SurfacePlatformAdapterPort,
    credentials: SurfaceCredentials,
    conversation_id: UUID,
    handle: DecisionCardHandle,
    reason: CardRetirement,
) -> None:
    try:
        await adapter.retire_decision_card(
            credentials=credentials, handle=handle, reason=reason
        )
    except platform_transport_errors():
        # The card keeps its buttons, and a tap on it is still answered
        # "already cancelled" -- worse than retiring it, not wrong.
        logger.warning(
            "agent_surfaces.card_retirement.card_not_retired.degraded",
            conversation_id=str(conversation_id),
            platform=handle.platform,
            exc_info=True,
        )


__all__ = ["retire_cards_answered_in_lemma", "retire_superseded_cards"]
