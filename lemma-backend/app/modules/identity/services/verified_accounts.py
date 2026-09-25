"""Resolve a durable mailbox proof to one canonical OSS auth identity."""

from __future__ import annotations

import secrets
from collections.abc import Awaitable, Callable
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import func, select
from supertokens_python.asyncio import list_users_by_account_info
from supertokens_python.recipe.emailpassword.asyncio import update_email_or_password
from supertokens_python.recipe.emailpassword.interfaces import (
    UpdateEmailOrPasswordOkResult,
)
from supertokens_python.recipe.emailverification.asyncio import (
    create_email_verification_token,
    verify_email_using_token,
)
from supertokens_python.recipe.emailverification.interfaces import (
    CreateEmailVerificationTokenOkResult,
)
from supertokens_python.recipe.passwordless.asyncio import signinup
from supertokens_python.recipe.session.asyncio import revoke_all_sessions_for_user
from supertokens_python.types import User as AuthUser
from supertokens_python.types.base import AccountInfoInput

from app.core.infrastructure.db.uow_factory import UnitOfWorkFactory
from app.modules.identity.domain.email_challenge import PENDING_TTL_SECONDS
from app.modules.identity.domain.errors import SignupNotAllowedError
from app.modules.identity.domain.user_entities import UserEntity
from app.modules.identity.infrastructure.identity_lease import identity_lease
from app.modules.identity.infrastructure.models.email_challenge_models import (
    EmailChallenge,
)
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.identity.infrastructure.user_cache import get_user_cache
from app.modules.identity.infrastructure.user_repositories import UserRepository
from app.modules.identity.services.email_challenges import (
    ChallengePurpose,
    ChallengeRejected,
    EmailChallengeService,
    _binding_hash,
)
from app.modules.identity.services.signup_gate import get_signup_gate

#: Raises `SignupNotAllowedError` to refuse a new account. Injected so a test
#: stands a gate in front of this function rather than patching the real one.
AdmitSignup = Callable[[str], Awaitable[object]]


async def _admit_signup(email: str) -> object:
    return await get_signup_gate().admit(email)


async def _auth_users(email: str) -> list[AuthUser]:
    return await list_users_by_account_info(
        "public", AccountInfoInput(email=email), do_union_of_account_info=False
    )


async def _secure_recovered_account(user: AuthUser, email: str) -> None:
    """Take the account back from whoever registered it first.

    Ordering is half of it, and only half. Revoking sessions does not prevent a
    *login*, so revoking before the password is replaced lets someone who
    preregistered this address sign in during the gap and keep a session the
    revoke has already swept past. Rotating first closes that.

    It does not close the mirror image: a sign-in whose credential was checked
    before the rotation can still have its session minted after the revoke, and
    a revoke only sweeps what already exists. No ordering here fixes that,
    because the gap is on the sign-in side. The caller holds
    `identity_lease(f"account:{email}")` for this whole function, and
    `sign_in_post` takes the same lease around verify-then-mint, so the two
    cannot interleave at all.
    """
    for method in user.login_methods:
        if method.recipe_id != "emailpassword":
            continue
        updated = await update_email_or_password(
            method.recipe_user_id,
            password=secrets.token_urlsafe(48),
            apply_password_policy=False,
        )
        if not isinstance(updated, UpdateEmailOrPasswordOkResult):
            raise ChallengeRejected("Password recovery could not finish; retry")
    await revoke_all_sessions_for_user(user.id)
    for method in user.login_methods:
        if method.has_same_email_as(email) and not method.verified:
            token = await create_email_verification_token(
                "public", method.recipe_user_id, email
            )
            if isinstance(token, CreateEmailVerificationTokenOkResult):
                await verify_email_using_token(
                    "public", token.token, attempt_account_linking=False
                )


async def complete_verified_account(
    uow_factory: UnitOfWorkFactory,
    *,
    operation_id: UUID,
    binding: str,
    purpose: ChallengePurpose,
    admit_signup: AdmitSignup = _admit_signup,
) -> UUID:
    digest = _binding_hash(binding, purpose)
    async with uow_factory() as uow:
        proof = await uow.session.get(EmailChallenge, operation_id)
        EmailChallengeService._require_bound(proof, digest, purpose)
        assert proof is not None
        if proof.verified_at is None or proof.verified_at + timedelta(
            seconds=PENDING_TTL_SECONDS
        ) <= datetime.now(timezone.utc):
            raise ChallengeRejected("Verify your email before continuing")
        email = proof.email

    async with identity_lease(f"account:{email}") as lease:
        async with uow_factory() as uow:
            local = await uow.session.scalar(
                select(User).where(func.lower(User.email) == email)
            )
            if local is not None and (not local.is_active or local.is_deleted):
                raise ChallengeRejected("This account cannot sign in")
            local_id = local.id if local else None
            locally_verified = bool(local and local.is_verified)
        # A code proves the mailbox, not a right to an account here. With no
        # local user this completion *creates* one -- it is how an email-code
        # sign-in and a chat onboarding sign up -- so it answers to the same
        # signup mode as a password or an OAuth provider does.
        if local_id is None:
            try:
                await admit_signup(email)
            except SignupNotAllowedError as refused:
                raise ChallengeRejected(refused.message) from refused
        users = await _auth_users(email)
        await lease.require_ownership()
        _require_canonical_identity(users, local_id)
        if not users:
            created = await signinup("public", email=email, phone_number=None)
            await lease.require_ownership()
            # Re-resolve Core's result before writing local state, including a
            # duplicate returned after an interrupted previous creation.
            users = await _auth_users(email)
            if len(users) != 1 or users[0].id != created.user.id:
                raise ChallengeRejected("Account creation needs to be retried")
        auth_user = users[0]
        if not locally_verified:
            await _secure_recovered_account(auth_user, email)
        await lease.require_ownership()
        user_id = UUID(auth_user.id)
        await _record_completed_account(
            uow_factory,
            operation_id=operation_id,
            digest=digest,
            purpose=purpose,
            user_id=user_id,
            email=email,
        )
        await get_user_cache().invalidate(user_id)
        await lease.require_ownership()
        return user_id


async def _record_completed_account(
    uow_factory: UnitOfWorkFactory,
    *,
    operation_id: UUID,
    digest: str,
    purpose: ChallengePurpose,
    user_id: UUID,
    email: str,
) -> None:
    async with uow_factory() as uow:
        proof = await uow.session.get(
            EmailChallenge, operation_id, with_for_update=True
        )
        EmailChallengeService._require_bound(proof, digest, purpose)
        assert proof is not None
        if proof.completed_user_id not in (None, user_id):
            raise ChallengeRejected("Conflicting verification completion")
        if proof.verified_at is None or proof.verified_at + timedelta(
            seconds=PENDING_TTL_SECONDS
        ) <= datetime.now(timezone.utc):
            raise ChallengeRejected("Verification expired; request another code")
        repository = UserRepository(uow)
        local_entity = await repository.get(user_id)
        if local_entity is None:
            local_entity = UserEntity(id=user_id, email=email, is_verified=False)
            local_entity.mark_email_verified()
            await repository.create(local_entity)
        else:
            if not local_entity.is_active or local_entity.is_deleted:
                raise ChallengeRejected("This account cannot sign in")
            local_entity.mark_email_verified()
            await repository.update(local_entity)
        proof.completed_user_id = user_id


def _require_canonical_identity(users: list[AuthUser], local_id: UUID | None) -> None:
    if len(users) > 1 or (
        local_id is not None and (not users or users[0].id != str(local_id))
    ):
        raise ChallengeRejected("Conflicting identities require account support")
