"""Expired signup content must disappear even when the sender never returns."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, update
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    OnboardingInputToken,
    PendingChatOnboarding,
)

logger = get_logger(__name__)

#: How long an expired signup row outlives its expiry before deletion. Long
#: enough that a platform redelivery still meets the `message_committed_at` that
#: makes it a no-op, short enough that "purged after expiry" is honest.
PURGE_GRACE_SECONDS = 24 * 60 * 60


async def purge_expired_onboarding(uows: UnitOfWorkFactory) -> None:
    now = datetime.now(timezone.utc)
    async with uows() as uow:
        await uow.session.execute(
            update(PendingChatOnboarding)
            .where(
                PendingChatOnboarding.expires_at <= now,
                PendingChatOnboarding.original_event.is_not(None),
            )
            .values(original_event=None)
        )
        await uow.session.execute(
            delete(OnboardingInputToken).where(OnboardingInputToken.expires_at <= now)
        )
        # And then the rows themselves. Nulling `original_event` drops the
        # message someone sent, but the row keeps their `verified_phone` and a
        # `destination` stamped with `sender_email` -- so "purged after handoff,
        # cancellation or expiry", which the operator guide promises, was only
        # half true and the half it left behind is the personal half.
        #
        # Not immediately: `message_committed_at` is what stops a replayed
        # delivery being answered twice, and it is only useful while a
        # redelivery is still plausible. The grace window is that, not a
        # retention policy.
        await uow.session.execute(
            delete(PendingChatOnboarding).where(
                PendingChatOnboarding.expires_at
                <= now - timedelta(seconds=PURGE_GRACE_SECONDS)
            )
        )


async def run_onboarding_cleanup(uows: UnitOfWorkFactory) -> None:
    while True:
        try:
            await purge_expired_onboarding(uows)
        except SQLAlchemyError:
            logger.warning("agent_surfaces.onboarding_cleanup.failed", exc_info=True)
        await asyncio.sleep(60)
