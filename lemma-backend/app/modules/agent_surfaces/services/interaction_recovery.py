"""What a failed tap leaves behind, and putting it right.

Split from `surface_interactions`: the failure path reads how far a submission
got and either hands its replay claim back and says it did not work, or does
nothing because the answer already reached the run.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable
from dataclasses import dataclass
from typing import Protocol

from app.core.log.log import get_logger
from app.modules.agent_surfaces.domain.entities import ParsedSurfaceInteraction
from app.modules.agent_surfaces.domain.ports import SurfaceEventDedupStorePort
from app.modules.agent_surfaces.services.interaction_helpers import (
    InteractionDelivery,
)

logger = get_logger(__name__)


class Acknowledge(Protocol):
    def __call__(
        self,
        parsed: ParsedSurfaceInteraction,
        delivery: InteractionDelivery,
        *,
        text: str,
        show_alert: bool = ...,
    ) -> Awaitable[None]: ...


@dataclass
class InteractionAttempt:
    """What one submission has done so far, for the failure path to read.

    A failure has to know how far the tap got: before the claim there is nothing
    to give back, after the answer reached the run there is nothing to undo.
    """

    delivery: InteractionDelivery | None = None
    #: The replay claim is held, and should be handed back if the work fails.
    claimed: bool = False
    #: The answer (or the retry) reached the run, so a repeat must stay a repeat.
    applied: bool = False


async def recover_from_failed_interaction(
    parsed: ParsedSurfaceInteraction,
    attempt: InteractionAttempt,
    *,
    event_dedup_store: SurfaceEventDedupStorePort,
    acknowledge: Acknowledge,
) -> None:
    """Make a failed tap retryable, and tell the person it did not work.

    Runs inside the failure handler, so it must not raise: anything that goes
    wrong here is logged and left, or the failure it is recovering from would
    be replaced by a less useful one.

    Once the answer has reached the run (``applied``) nothing is undone and
    nothing is said -- a failed *acknowledgement* is not a failed action, and
    giving the claim back would let a second tap answer the same question
    twice.
    """
    if attempt.applied or attempt.delivery is None:
        return
    _thread, surface, _adapter, _credentials = attempt.delivery

    async def hand_the_replay_claim_back() -> None:
        if attempt.claimed:
            await event_dedup_store.release_message(
                surface_installation_id=surface.id,
                platform=surface.surface_type,
                external_channel_id=parsed.external_channel_id,
                external_thread_id=parsed.external_thread_id,
                external_message_id=parsed.dedup_id,
            )

    # Gathered rather than awaited in turn, so one step failing cannot stop
    # the other: a Redis outage must not leave the person without their
    # answer, nor a Slack one leave the claim spent.
    released, acknowledged = await asyncio.gather(
        hand_the_replay_claim_back(),
        acknowledge(
            parsed,
            attempt.delivery,
            text="I couldn’t complete that action.",
            show_alert=True,
        ),
        return_exceptions=True,
    )
    if isinstance(released, Exception):
        logger.warning(
            "agent_surfaces.ingress_service.surface_interaction_claim_release_failed.degraded",
            surface_id=str(surface.id),
            exc_info=released,
        )
    if isinstance(acknowledged, Exception):
        logger.warning(
            "agent_surfaces.ingress_service.surface_interaction_failure_unacknowledged.degraded",
            surface_id=str(surface.id),
            exc_info=acknowledged,
        )
