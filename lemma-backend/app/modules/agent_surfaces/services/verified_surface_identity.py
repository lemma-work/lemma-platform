from __future__ import annotations

from uuid import UUID
from sqlalchemy import select

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfacePlatform,
)
from app.modules.agent_surfaces.infrastructure.onboarding_models import (
    VerifiedSurfaceIdentity,
)
from app.modules.agent_surfaces.services.onboarding_transport import (
    platform_binding_key,
)
from app.modules.identity.contracts.onboarding import active_chat_user


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
        or (
            event.platform in (SurfacePlatform.WHATSAPP, SurfacePlatform.TELEGRAM)
            and (
                not identity.verified_phone
                or identity.verified_phone != user.mobile_number
                or user.mobile_verified_at is None
            )
        )
    ):
        return ResolvedSurfaceUser(external_user_id=event.sender_external_user_id)
    return ResolvedSurfaceUser(
        internal_user_id=user.id,
        external_user_id=event.sender_external_user_id,
        email=str(user.email),
        phone=user.mobile_number,
        display_name=user.first_name,
    )
