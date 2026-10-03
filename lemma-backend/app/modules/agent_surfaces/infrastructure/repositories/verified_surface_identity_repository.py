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
        self,
        user_id: UUID,
        current_phone: str | None,
        *,
        previous_phone: str | None = None,
    ) -> None:
        """Revoke every phone-bound identity the account no longer backs.

        With no verified number every phone-bound identity goes; otherwise only
        the ones bound to some other number. The ``None`` arm has to be written
        as a SQL literal -- a plain Python bool inside ``or_`` reads as SQL and
        is not.

        `previous_phone`, when the change says what it replaced, narrows that to
        the bindings resting on the replaced number. A WhatsApp binding proves a
        number of its own, which need never have been the profile's; retiring it
        because the profile moved from one *other* number to another would sign
        out a phone nobody gave up.
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
                *(
                    (VerifiedSurfaceIdentity.verified_phone == previous_phone,)
                    if previous_phone is not None
                    else ()
                ),
            )
            # The destination goes with the proof: `ck_surface_identity_route_
            # is_live` refuses a revoked row that still carries one.
            .values(
                revoked_at=datetime.now(timezone.utc),
                installation_surface_id=None,
                pod_id=None,
            )
        )

    async def revoke_phone_held_by_others(self, user_id: UUID, phone: str) -> None:
        """Retire other accounts' bindings on a number this account just proved.

        The newest proof of a number wins. Without this, a number that changed
        hands kept answering as its previous holder: their binding still named
        it, and a binding is recognised before anything else is asked.
        """
        await self.session.execute(
            update(VerifiedSurfaceIdentity)
            .where(
                VerifiedSurfaceIdentity.user_id != user_id,
                VerifiedSurfaceIdentity.verified_phone == phone,
                VerifiedSurfaceIdentity.revoked_at.is_(None),
            )
            .values(
                revoked_at=datetime.now(timezone.utc),
                installation_surface_id=None,
                pod_id=None,
            )
        )
