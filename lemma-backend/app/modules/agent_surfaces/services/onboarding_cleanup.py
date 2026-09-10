"""Expired signup content must disappear even when the sender never returns."""

import asyncio
from datetime import datetime, timezone

from sqlalchemy import delete, update
from sqlalchemy.exc import SQLAlchemyError

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.core.log.log import get_logger
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    OnboardingInputToken,
    PendingChatOnboarding,
)

logger = get_logger(__name__)


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


async def run_onboarding_cleanup(uows: UnitOfWorkFactory) -> None:
    while True:
        try:
            await purge_expired_onboarding(uows)
        except SQLAlchemyError:
            logger.warning("agent_surfaces.onboarding_cleanup.failed", exc_info=True)
        await asyncio.sleep(60)
