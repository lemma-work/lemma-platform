"""The claim that makes one inbound message be handled once.

A platform may deliver the same message more than once, and the inbox may
retry a delivery that half-failed. The claim is a Redis key per message: taken
when the message is in hand, handed back when preparation fails, so a retry is
dropped only when the first attempt really did the work.

Free functions over the session and the store rather than methods on the
ingress mixin, which is at the file-size ceiling and has plenty to say without
the plumbing of a claim.
"""

from __future__ import annotations

from app.core.infrastructure.db.transaction_locks import connection_released
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import (
    AgentSurfaceEntity,
    ParsedInboundSurfaceEvent,
)
from app.modules.agent_surfaces.domain.ports import SurfaceEventDedupStorePort

logger = get_logger(__name__)


async def take_delivery_claim(
    uow: SqlAlchemyUnitOfWork,
    store: SurfaceEventDedupStorePort,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
) -> bool:
    """Take the delivery claim, or say this message was already taken.

    Claimed only with the message in hand: claiming earlier burns it on an
    attempt that had no body, so the retry is discarded as a duplicate.
    Enrichment also changes the ids this keys on. Replay re-runs a message
    the claim already burned, so it asks for the claim to be skipped; every
    live delivery still takes it.
    """
    # The connection goes back for the claim itself: it is a Redis round
    # trip, and only reads have happened by here -- the identity upsert and
    # the conversation link come after, so this release is real rather than
    # a `safe_to_release` no-op.
    async with connection_released(uow.session):
        claimed = await store.claim_message(
            surface_installation_id=surface.id,
            platform=surface.surface_type,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=parsed.external_thread_id,
            external_message_id=parsed.external_message_id,
        )
    if not claimed:
        logger.debug(
            "agent_surfaces.ingress_service.agent_surface_ignored_duplicate_external.observed",
            surface_type=surface.surface_type,
            external_channel_id=parsed.external_channel_id,
        )
    return claimed


async def hand_back_delivery_claim(
    uow: SqlAlchemyUnitOfWork,
    store: SurfaceEventDedupStorePort,
    surface: AgentSurfaceEntity,
    parsed: ParsedInboundSurfaceEvent,
) -> None:
    """Hand the claim back, so the inbox's retry is not read as a duplicate."""
    # Redis: the connection goes back where nothing was written.
    async with connection_released(uow.session):
        await store.release_message(
            surface_installation_id=surface.id,
            platform=surface.surface_type,
            external_channel_id=parsed.external_channel_id,
            external_thread_id=parsed.external_thread_id,
            external_message_id=parsed.external_message_id,
        )
