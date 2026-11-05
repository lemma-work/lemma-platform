"""The rows a chat signup keeps while it is under way, and their cleanup."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import delete, or_, select, update

from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    OnboardingInputToken,
    PendingChatOnboarding,
)


class ChatOnboardingRepository:
    """Pending signups and the one-time input tokens their forms carry."""

    def __init__(self, uow: IUnitOfWork):
        self.session = uow.session

    async def pending_by_binding_key(
        self, binding_key: str
    ) -> PendingChatOnboarding | None:
        return await self.session.scalar(
            select(PendingChatOnboarding).where(
                PendingChatOnboarding.binding_key == binding_key
            )
        )

    async def pending_by_id(self, pending_id: UUID) -> PendingChatOnboarding | None:
        return await self.session.get(PendingChatOnboarding, pending_id)

    async def discard_pending(self, row: PendingChatOnboarding) -> None:
        """Delete a pending signup, flushed so a new one may take its key."""
        await self.session.delete(row)
        await self.session.flush()

    async def input_token_by_hash(self, token_hash: str) -> OnboardingInputToken | None:
        return await self.session.scalar(
            select(OnboardingInputToken).where(
                OnboardingInputToken.token_hash == token_hash
            )
        )

    async def purge_expired(
        self,
        now: datetime,
        *,
        grace: timedelta,
        attached_grace: timedelta,
    ) -> None:
        """Drop what expired signups keep, then the rows, after their graces.

        A row that never reached an account is finished with once ``grace`` has
        passed; one that did and has not been handed off may still be owed
        something, and waits ``attached_grace``.
        """
        await self.session.execute(
            update(PendingChatOnboarding)
            .where(
                PendingChatOnboarding.expires_at <= now,
                PendingChatOnboarding.original_event.is_not(None),
            )
            .values(original_event=None)
        )
        await self.session.execute(
            delete(OnboardingInputToken).where(OnboardingInputToken.expires_at <= now)
        )
        await self.session.execute(
            delete(PendingChatOnboarding).where(
                PendingChatOnboarding.expires_at <= now - grace,
                or_(
                    PendingChatOnboarding.user_id.is_(None),
                    PendingChatOnboarding.handed_off_at.is_not(None),
                ),
            )
        )
        await self.session.execute(
            delete(PendingChatOnboarding).where(
                PendingChatOnboarding.expires_at <= now - attached_grace
            )
        )
