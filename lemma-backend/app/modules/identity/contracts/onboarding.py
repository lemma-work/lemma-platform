"""Chat entry points carry a verified challenge, never an asserted email."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID
from contextlib import AbstractAsyncContextManager

from app.core.helpers.identifiers import normalize_mobile_e164
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.identity.domain.events import (
    UserMobileChangedEvent,
    UserPhoneReplacedEvent,
)
from app.modules.identity.domain.user_entities import UserEntity
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.modules.identity.infrastructure.adapters.email_adapter import (
    SmtpIdentityEmailAdapter,
)
from app.modules.identity.infrastructure.mobile_number_claims import (
    acquire_mobile_number_claim_lock,
    get_other_mobile_number_owner_id,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.user_cache import get_user_cache
from app.modules.identity.services.auth_abuse import RateLimitExceeded
from app.modules.identity.services.email_challenge_limits import (
    enforce_challenge_send_limits,
)
from app.modules.identity.services.email_challenges import (
    ChallengeRejected,
    EmailChallengeService,
)
from app.modules.identity.services.first_workspace import (
    ProvisionedWorkspace,
    ensure_first_workspace,
)
from app.modules.identity.services.verified_accounts import complete_verified_account
from app.modules.identity.domain.email_challenge import (
    PENDING_TTL_SECONDS,
    parse_email_reply,
)
from app.modules.identity.infrastructure.identity_lease import (
    IdentityLease,
    identity_lease,
)

__all__ = [
    "ChallengeRejected",
    "RateLimitExceeded",
    "EmailChallengeService",
    "PENDING_TTL_SECONDS",
    "parse_email_reply",
    "hold_chat_onboarding",
    "email_challenge_service",
    "complete_chat_account",
    "ensure_chat_workspace",
    "current_verified_phone",
    "active_chat_user",
]


def hold_chat_onboarding(
    binding_key: str,
) -> AbstractAsyncContextManager[IdentityLease]:
    return identity_lease(f"chat-onboarding:{binding_key}")


def email_challenge_service(surface_label: str) -> EmailChallengeService:
    adapter = SmtpIdentityEmailAdapter()

    async def send(*, email: str, code: str) -> bool:
        return await adapter.send_chat_signup_code_email(
            to_email=email, code=code, surface_label=surface_label
        )

    return EmailChallengeService(
        async_session_maker,
        send_email=send,
        enforce_send_limits=enforce_challenge_send_limits,
    )


async def complete_chat_account(
    uow_factory: UnitOfWorkFactory, *, challenge_id: UUID, binding: str
) -> UUID:
    return await complete_verified_account(
        uow_factory,
        operation_id=challenge_id,
        binding=binding,
        purpose="chat_onboarding",
    )


async def ensure_chat_workspace(
    uow_factory: UnitOfWorkFactory,
    *,
    user_id: UUID,
    verified_phone: str | None,
    full_name: str | None,
    installation_organization_id: UUID | None,
) -> ProvisionedWorkspace:
    from app.modules.identity.api.dependencies import get_organization_service

    async with uow_factory() as uow:
        user = await uow.session.get(User, user_id, with_for_update=True)
        if (
            user is None
            or not user.is_active
            or user.is_deleted
            or not user.is_verified
        ):
            raise ChallengeRejected("A verified active account is required")
        if verified_phone:
            normalized = normalize_mobile_e164(verified_phone)
            digits = normalized.lstrip("+")
            await acquire_mobile_number_claim_lock(uow.session, digits)
            if await get_other_mobile_number_owner_id(
                uow.session, digits=digits, user_id=user_id
            ):
                raise ChallengeRejected("This phone belongs to another account")
            if user.mobile_number != normalized:
                user.mobile_number = normalized
                uow.collect_events(
                    [
                        UserMobileChangedEvent(user_id=user_id),
                        UserPhoneReplacedEvent(
                            user_id=user_id, email=user.email, mobile_number=normalized
                        ),
                    ]
                )
            user.mobile_verified_at = datetime.now(timezone.utc)
        if full_name and not user.first_name:
            user.first_name, _, last = full_name.strip().partition(" ")
            user.last_name = last or None
        email = user.email
        name = " ".join(part for part in (user.first_name, user.last_name) if part)
    await get_user_cache().invalidate(user_id)
    async with uow_factory() as uow:
        return await ensure_first_workspace(
            uow,
            organization_service=get_organization_service(uow),
            user_id=user_id,
            email=email,
            full_name=name or None,
            arrived_through_organization_id=installation_organization_id,
        )


async def current_verified_phone(
    uow_factory: UnitOfWorkFactory, user_id: UUID
) -> str | None:
    async with uow_factory() as uow:
        user = await uow.session.get(User, user_id)
        return (
            user.mobile_number
            if user is not None and user.mobile_verified_at is not None
            else None
        )


async def active_chat_user(
    uow: SqlAlchemyUnitOfWork, user_id: UUID
) -> UserEntity | None:
    user = await uow.session.get(User, user_id)
    if user is None or not user.is_active or user.is_deleted or not user.is_verified:
        return None
    return user.to_entity()
