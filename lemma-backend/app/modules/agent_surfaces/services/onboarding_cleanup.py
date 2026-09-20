"""Expired signup content must disappear even when the sender never returns."""

import asyncio
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, or_, update
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

#: The same, for a row that has an account attached and has not finished. The
#: coordinator stops expiring a row the moment `user_id` is set, and it stops
#: for a reason: ORGANIZATION_ACCESS_REQUIRED is a verified person waiting on
#: an administrator, and outlasting a human being is the entire point of the
#: state. This deleted them anyway -- one deadline, read off `expires_at` alone
#: -- so the wait the coordinator promised was over in about a day, and what
#: came back was "Setup expired", after they had done everything asked of them.
#:
#: Still a deadline rather than none. Nobody is coming after a month, and a row
#: kept forever is somebody's phone number and mailbox kept forever.
ATTACHED_PURGE_GRACE_SECONDS = 30 * 24 * 60 * 60


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
        #
        # Two deadlines, because two different rows end up here. A row that
        # never reached an account is finished with, and a row that did may
        # still be owed something: `handed_off_at` is what every ending sets --
        # cancelled, expired, refused, replayed -- so its absence beside a
        # `user_id` is precisely the signup still waiting on somebody.
        await uow.session.execute(
            delete(PendingChatOnboarding).where(
                PendingChatOnboarding.expires_at
                <= now - timedelta(seconds=PURGE_GRACE_SECONDS),
                or_(
                    PendingChatOnboarding.user_id.is_(None),
                    PendingChatOnboarding.handed_off_at.is_not(None),
                ),
            )
        )
        await uow.session.execute(
            delete(PendingChatOnboarding).where(
                PendingChatOnboarding.expires_at
                <= now - timedelta(seconds=ATTACHED_PURGE_GRACE_SECONDS)
            )
        )


async def run_onboarding_cleanup(uows: UnitOfWorkFactory) -> None:
    while True:
        try:
            await purge_expired_onboarding(uows)
        except SQLAlchemyError:
            logger.warning("agent_surfaces.onboarding_cleanup.failed", exc_info=True)
        await asyncio.sleep(60)
