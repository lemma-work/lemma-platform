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
from app.modules.agent_surfaces.platforms.platform_capabilities import (
    has_shared_system_bot,
)
from app.modules.agent_surfaces.services.onboarding_transport import (
    platform_binding_key,
)
from app.modules.identity.contracts.onboarding import UserEntity, active_chat_user


#: Platforms whose binding proves its own phone number. A WhatsApp message comes
#: *from* the number, and Telegram's contact share is accepted only when the
#: sender shares their own contact, so the number on the binding was proven by
#: the platform at the moment it was bound -- it never needed the profile to
#: agree with it.
_SELF_PROVING = frozenset(
    {SurfacePlatform.WHATSAPP.value, SurfacePlatform.TELEGRAM.value}
)


def phone_binding_holds(
    identity: VerifiedSurfaceIdentity,
    user: UserEntity,
    platform: str | SurfacePlatform,
) -> bool:
    """Does this binding's phone proof still stand for this account?

    It used to be "the binding's number is the profile's verified number", and
    that made the profile the only place a person could have one phone. An
    existing user who signed up on WhatsApp from a second number had their web
    profile number *overwritten* to make the check pass -- and every other
    binding resting on the old number was revoked by the change. Now a
    self-proving binding stands on its own proof; what retires it is an event,
    not a comparison: the profile giving up the number it rested on, or
    another account proving the number (`on_identity_event`).

    A binding with no phone is not phone-bound, and holds. Other platforms that
    carry one keep the old rule.
    """
    if identity.verified_phone is None:
        return True
    if str(getattr(platform, "value", platform)).upper() in _SELF_PROVING:
        return True
    return (
        identity.verified_phone == user.mobile_number
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
        or (has_shared_system_bot(event.platform) and not identity.verified_phone)
        or not phone_binding_holds(identity, user, event.platform)
    ):
        return ResolvedSurfaceUser(external_user_id=event.sender_external_user_id)
    return ResolvedSurfaceUser(
        internal_user_id=user.id,
        external_user_id=event.sender_external_user_id,
        email=str(user.email),
        phone=user.mobile_number,
        display_name=user.first_name,
    )
