"""Chat entry points carry a verified challenge, never an asserted email."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID
from contextlib import AbstractAsyncContextManager

from sqlalchemy import select

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
    proven_claim_blocker,
)
from app.modules.identity.infrastructure.chat_account_policy import is_chat_account
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
from app.modules.identity.services.verified_accounts import (
    ACCOUNT_RETRY,
    PERMANENT_ACCOUNT_REFUSALS,
    complete_verified_account,
)
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
    "ACCOUNT_RETRY",
    "PERMANENT_ACCOUNT_REFUSALS",
    "PHONE_OWNED_ELSEWHERE",
    "account_created_since",
    "claim_chat_phone",
    "complete_chat_account",
    "ensure_chat_workspace",
    "ensure_chat_organization",
    "current_verified_phone",
    "active_chat_user",
]


def hold_chat_onboarding(
    binding_key: str,
) -> AbstractAsyncContextManager[IdentityLease]:
    return identity_lease(f"chat-onboarding:{binding_key}")


def email_challenge_service(surface_label: str) -> EmailChallengeService:
    adapter = SmtpIdentityEmailAdapter()

    async def send_challenge_code(*, email: str, code: str) -> bool:
        return await adapter.send_chat_signup_code_email(
            to_email=email, code=code, surface_label=surface_label
        )

    return EmailChallengeService(
        async_session_maker,
        send_email=send_challenge_code,
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


#: The refusal a chat signup gets when the number it proved is verified on a
#: different, live account. Its own code because it is permanent for this
#: signup and needs an action somewhere else -- the other account -- to clear.
PHONE_OWNED_ELSEWHERE = "PHONE_OWNED_ELSEWHERE"


async def claim_chat_phone(
    uow_factory: UnitOfWorkFactory,
    *,
    user_id: UUID,
    verified_phone: str | None,
    full_name: str | None,
) -> None:
    """Record what a chat signup proved about this account, before anything else.

    The top half of what `ensure_chat_workspace` used to do, separated because
    the workspace half is now a choice -- an existing pod, a list, or a new one
    -- and this half is the same whichever it turns out to be.

    The number is the sender's own (a WhatsApp message comes *from* it) or a
    contact the sender shared of themselves, so it is proof. Two rules follow:

    * Only a live, verified account outranks it. Stale holders -- deleted or
      deactivated accounts -- are released under the claim lock rather than
      reported as owners nobody can do anything about.
    * It never overwrites a *different* number already on the profile. The chat
      binding carries its own proof (`VerifiedSurfaceIdentity.verified_phone`),
      so an existing user who signs up on WhatsApp from a second phone keeps the
      number they verified on the web. Overwriting it silently signed them out
      of every binding that rested on the old one. The profile is filled in only
      when it has no number, or stamped verified when it already held this one.
    """
    async with uow_factory() as uow:
        user = await uow.session.get(User, user_id, with_for_update=True)
        if user is None or not is_chat_account(user):
            raise ChallengeRejected("A verified active account is required")
        if verified_phone:
            normalized = normalize_mobile_e164(verified_phone)
            digits = normalized.lstrip("+")
            await acquire_mobile_number_claim_lock(uow.session, digits)
            if await proven_claim_blocker(uow.session, digits=digits, user_id=user_id):
                raise ChallengeRejected(
                    "This phone number is verified on another Lemma account. "
                    "Remove it from that account's profile (Settings → Profile), "
                    "or sign up with that account's email instead",
                    code=PHONE_OWNED_ELSEWHERE,
                )
            _stamp_phone(uow, user, normalized)
        if full_name and not user.first_name:
            user.first_name, _, last = full_name.strip().partition(" ")
            user.last_name = last or None
    await get_user_cache().invalidate(user_id)


def _stamp_phone(uow: SqlAlchemyUnitOfWork, user: User, normalized: str) -> None:
    """Put a proven number on a profile that has none, or confirm the one it has."""
    if user.mobile_number is None:
        user.mobile_number = normalized
        user.mobile_verified_at = datetime.now(timezone.utc)
        uow.collect_events(
            [
                UserMobileChangedEvent(user_id=user.id, previous_mobile_number=None),
                UserPhoneReplacedEvent(
                    user_id=user.id, email=user.email, mobile_number=normalized
                ),
            ]
        )
    elif normalize_mobile_e164(user.mobile_number) == normalized:
        user.mobile_number = normalized
        if user.mobile_verified_at is None:
            user.mobile_verified_at = datetime.now(timezone.utc)


async def ensure_chat_workspace(
    uow_factory: UnitOfWorkFactory,
    *,
    user_id: UUID,
    installation_organization_id: UUID | None,
) -> ProvisionedWorkspace:
    """Run the first-workspace policy for a chat signup with nothing to attach.

    Only reached once the person has no pod a chat can use: anyone with one is
    attached to it or asked which. So this is the "make one" branch, and its
    result can say no -- `organization_access_required` or `pod_limit_reached`
    -- which the caller turns into words rather than an assertion.
    """
    from app.modules.identity.api.dependencies import get_organization_service

    async with uow_factory() as uow:
        user = await uow.session.get(User, user_id)
        if user is None or not is_chat_account(user):
            raise ChallengeRejected("A verified active account is required")
        email = user.email
        name = " ".join(part for part in (user.first_name, user.last_name) if part)
    async with uow_factory() as uow:
        return await ensure_first_workspace(
            uow,
            organization_service=get_organization_service(uow),
            user_id=user_id,
            email=email,
            full_name=name or None,
            arrived_through_organization_id=installation_organization_id,
        )


async def account_created_since(
    uow_factory: UnitOfWorkFactory, *, user_id: UUID, since: datetime
) -> bool:
    """Was this account made after `since` -- that is, by the signup that began then?

    The confirmation a chat signup ends with is different for the two: a new
    account has one passwordless way in and has to be told so, while an
    existing one should simply sign in the way it always has. Read from the
    account's own creation time rather than carried through the signup, so a
    retry or a crash between the code and the confirmation cannot lose it.
    """
    async with uow_factory() as uow:
        created_at = await uow.session.scalar(
            select(User.created_at).where(User.id == user_id)
        )
    return created_at is not None and created_at >= since


async def accept_chat_invitations(
    uow_factory: UnitOfWorkFactory, *, user_id: UUID
) -> UUID | None:
    """Honour a recognised chat user's pending invitations before offering pods.

    Somebody who already has an account and messages from a new chat is asked
    which workspace to use; a pod they were invited to belongs on that list,
    and accepting it is what puts it there. Only a verified address may.

    Returns the pod an invitation named, if one did, so the confirmation can
    say "you were invited to ..." rather than just naming a pod they may never
    have heard of.
    """
    from app.modules.identity.api.dependencies import get_organization_service
    from app.modules.identity.services.pending_invitations import (
        accept_pending_invitations,
    )

    async with uow_factory() as uow:
        user = await uow.session.get(User, user_id)
        if user is None or not user.is_verified:
            return None
        accepted = await accept_pending_invitations(
            uow,
            organization_service=get_organization_service(uow),
            user_id=user_id,
            email=user.email,
        )
    return accepted.pod_id if accepted is not None else None


async def ensure_chat_organization(
    uow: SqlAlchemyUnitOfWork, *, user_id: UUID
) -> UUID | None:
    """The organization a chat-first workspace goes in, making one if there is none.

    The same policy the web signup runs -- an existing membership, else a join
    by verified work domain, else a new organization of their own -- asked for
    the organization alone, because the caller is about to create a *named* pod
    and the spare one `with_pod` would make is clutter beside it.

    Only for the shared bot. A company installation fixes the organization, and
    somebody outside it is told to ask an administrator rather than handed a
    workspace next door to the one they meant.
    """
    from app.modules.identity.api.dependencies import get_organization_service

    user = await uow.session.get(User, user_id)
    if user is None:
        return None
    workspace = await ensure_first_workspace(
        uow,
        organization_service=get_organization_service(uow),
        user_id=user_id,
        email=user.email,
        with_pod=False,
    )
    return workspace.organization_id


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
    if user is None or not is_chat_account(user):
        return None
    return user.to_entity()
