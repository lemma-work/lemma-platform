from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import false, select, update

from app.core.domain.uow import IUnitOfWork
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    VerifiedSurfaceIdentity,
)


class VerifiedSurfaceIdentityRepository:
    """The verified identities a person has proven on a chat platform."""

    def __init__(self, uow: IUnitOfWork):
        self.session = uow.session

    async def by_binding_key(
        self, binding_key: str, *, include_revoked: bool = True
    ) -> VerifiedSurfaceIdentity | None:
        """The identity bound to this platform address, revoked or not unless
        told otherwise."""
        query = select(VerifiedSurfaceIdentity).where(
            VerifiedSurfaceIdentity.binding_key == binding_key
        )
        if not include_revoked:
            query = query.where(VerifiedSurfaceIdentity.revoked_at.is_(None))
        return await self.session.scalar(query)

    async def personal_route(self, binding_key: str) -> tuple[UUID, UUID] | None:
        """(identity id, installation surface id) of a routable personal DM.

        The whole of `is_routable`, not the pod alone. The installation FK is
        SET NULL on purpose -- deleting a company's app leaves the proof of
        identity and clears the destination -- so a row with a pod and no
        installation reads as "no route" here rather than bypassing the
        workspace choice and failing validation later.
        """
        found = (
            await self.session.execute(
                select(
                    VerifiedSurfaceIdentity.id,
                    VerifiedSurfaceIdentity.installation_surface_id,
                ).where(
                    VerifiedSurfaceIdentity.binding_key == binding_key,
                    VerifiedSurfaceIdentity.installation_surface_id.is_not(None),
                    VerifiedSurfaceIdentity.revoked_at.is_(None),
                    VerifiedSurfaceIdentity.pod_id.is_not(None),
                )
            )
        ).first()
        if found is None or found[1] is None:
            return None
        return found[0], found[1]

    async def revoke_phone_bound_except(
        self, user_id: UUID, current_phone: str | None
    ) -> None:
        """Revoke every phone-bound identity the account no longer backs.

        With no verified number every phone-bound identity goes; otherwise only
        the ones bound to some other number. The ``None`` arm has to be written
        as a SQL literal -- a plain Python bool inside ``or_`` reads as SQL and
        is not.

        One its owner also proved from a signed-in session stands on that
        proof: it loses the number it no longer has, and stays.
        """
        still_bound = (
            VerifiedSurfaceIdentity.verified_phone == current_phone
            if current_phone is not None
            else false()
        )
        stale = (
            VerifiedSurfaceIdentity.user_id == user_id,
            VerifiedSurfaceIdentity.verified_phone.isnot(None),
            ~still_bound,
        )
        await self.session.execute(
            update(VerifiedSurfaceIdentity)
            .where(*stale, VerifiedSurfaceIdentity.session_proved_at.isnot(None))
            .values(verified_phone=None)
        )
        await self.session.execute(
            update(VerifiedSurfaceIdentity)
            .where(*stale, VerifiedSurfaceIdentity.session_proved_at.is_(None))
            .values(revoked_at=datetime.now(timezone.utc))
        )

    async def prove_by_session(
        self,
        *,
        binding_key: str,
        platform: str,
        external_user_id: str,
        user_id: UUID,
        surface_id: UUID,
    ) -> VerifiedSurfaceIdentity | None:
        """Bind a platform account to the person who proved it from Lemma.

        ``None`` when the account is already somebody else's, live: proving it
        again from another session is not how an account changes hands. A
        revoked binding is nobody's, and is taken over with nothing of its
        previous owner's left on it.
        """
        identity = await self.by_binding_key(binding_key)
        if identity is not None and identity.user_id != user_id:
            if identity.revoked_at is None:
                return None
            identity.user_id = user_id
            identity.verified_phone = None
        if identity is None:
            identity = VerifiedSurfaceIdentity(
                binding_key=binding_key,
                platform=platform,
                tenant_id="",
                external_user_id=external_user_id,
                user_id=user_id,
            )
            self.session.add(identity)
        identity.revoked_at = None
        identity.session_proved_at = datetime.now(timezone.utc)
        identity.proved_through_surface_id = surface_id
        await self.session.flush()
        return identity
