from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import false, update

from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    VerifiedSurfaceIdentity,
)


class VerifiedSurfaceIdentityRepository:
    """Writes to the verified identities a person has proven on a chat platform."""

    def __init__(self, uow: IUnitOfWork):
        self.session = uow.session

    async def revoke_phone_bound_except(
        self, user_id: UUID, current_phone: str | None
    ) -> None:
        """Revoke every phone-bound identity the account no longer backs.

        With no verified number every phone-bound identity goes; otherwise only
        the ones bound to some other number. The ``None`` arm has to be written
        as a SQL literal -- a plain Python bool inside ``or_`` reads as SQL and
        is not.
        """
        still_bound = (
            VerifiedSurfaceIdentity.verified_phone == current_phone
            if current_phone is not None
            else false()
        )
        await self.session.execute(
            update(VerifiedSurfaceIdentity)
            .where(
                VerifiedSurfaceIdentity.user_id == user_id,
                VerifiedSurfaceIdentity.verified_phone.isnot(None),
                ~still_bound,
            )
            .values(revoked_at=datetime.now(timezone.utc))
        )
