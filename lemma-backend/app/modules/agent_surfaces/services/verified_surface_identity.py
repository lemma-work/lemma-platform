from __future__ import annotations

from uuid import UUID
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfacePlatform,
)
from app.modules.agent_surfaces.domain.onboarding_state import IdentityProof
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.services.onboarding_transport import (
    platform_binding_key,
)
from app.modules.identity.contracts.onboarding import UserEntity, active_chat_user


def proof_still_holds(identity: VerifiedSurfaceIdentity, user: UserEntity) -> bool:
    """Whether what proved this identity is still true of the account.

    One answer for both readers -- onboarding's `verified_sender` and
    ingestion's `resolve_shared_verified_identity` -- because they used to
    disagree: onboarding checked the phone only when the row had one, ingestion
    demanded one on every shared-bot row, so an identity proven without a phone
    was recognised by one and a stranger to the other.

    A phone proof is held to the account's current, verified mobile number: the
    number is the proof, so a number that has moved on proves nothing. An email
    code or an app-minted link proved the account itself, and has nothing to
    be held to.
    """
    if identity.proof != IdentityProof.PHONE:
        return True
    return (
        bool(identity.verified_phone)
        and identity.verified_phone == user.mobile_number
        and user.mobile_verified_at is not None
    )


async def resolve_shared_verified_identity(
    uow: SqlAlchemyUnitOfWork,
    event: ParsedInboundSurfaceEvent,
    installation_id: UUID | None = None,
) -> ResolvedSurfaceUser | None:
    if (
        not event.sender_external_user_id
        or (
            event.platform in (SurfacePlatform.SLACK, SurfacePlatform.TEAMS)
            and installation_id is None
        )
        or event.platform.is_email
    ):
        return None
    identity = await uow.session.scalar(
        select(VerifiedSurfaceIdentity).where(
            VerifiedSurfaceIdentity.binding_key
            == platform_binding_key(event, installation_id)
        )
    )
    if identity is None:
        return None
    user = await active_chat_user(uow, identity.user_id)
    if (
        identity.revoked_at is not None
        or user is None
        or not proof_still_holds(identity, user)
    ):
        return ResolvedSurfaceUser(external_user_id=event.sender_external_user_id)
    return ResolvedSurfaceUser(
        internal_user_id=user.id,
        external_user_id=event.sender_external_user_id,
        email=str(user.email),
        phone=user.mobile_number,
        display_name=user.first_name,
    )
