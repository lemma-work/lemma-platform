from __future__ import annotations

from app.modules.agent_surfaces.infrastructure.repositories.verified_surface_identity_repository import (
    VerifiedSurfaceIdentityRepository,
)

from uuid import UUID

from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.agent_surfaces.domain.entities import (
    ParsedInboundSurfaceEvent,
    ResolvedSurfaceUser,
    SurfacePlatform,
)
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    has_shared_system_bot,
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
    identity = await VerifiedSurfaceIdentityRepository(uow).by_binding_key(
        platform_binding_key(event, installation_id)
    )
    if identity is None:
        return None
    user = await active_chat_user(uow, identity.user_id)
    if (
        identity.revoked_at is not None
        or user is None
        or (
            # A shared bot's binding stands on the number the platform
            # attested. One made through an org's own app stands on the mailbox
            # code alone: that app's numbers are its secret holder's word, so
            # none was recorded.
            has_shared_system_bot(event.platform)
            and installation_id is None
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
