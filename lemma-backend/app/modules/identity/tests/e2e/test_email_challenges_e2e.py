"""Mailbox proof against Postgres, Redis and unlicensed SuperTokens."""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from supertokens_python.asyncio import get_user

from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.identity.services.email_challenges import (
    ChallengeRejected,
    EmailChallengeService,
)
from app.modules.identity.services.verified_accounts import complete_verified_account
from app.modules.identity.infrastructure.models.email_challenge_models import (
    EmailChallenge,
)

pytestmark = [pytest.mark.e2e, pytest.mark.asyncio]


class Mailbox:
    def __init__(self) -> None:
        self.code = ""

    async def send(self, *, email: str, code: str) -> bool:
        self.code = code
        return True


async def allow_test_delivery(*, email: str, sender_key: str) -> None:
    """These cases exercise proof; send limits have independent coverage."""


async def test_independent_platform_challenges_converge_on_one_account(
    db_session, async_client
):
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    mailbox = Mailbox()
    service = EmailChallengeService(
        sessions, send_email=mailbox.send, enforce_send_limits=allow_test_delivery
    )
    email = f"concurrent-{uuid4().hex}@gmail.com"
    operations = []
    for purpose in ("browser_login", "chat_onboarding"):
        binding = uuid4().hex
        receipt = await service.start(
            email=email, binding=binding, purpose=purpose, sender_key=binding
        )
        await service.verify(
            challenge_id=receipt.id,
            binding=binding,
            purpose=purpose,
            submitted_code=mailbox.code,
        )
        operations.append((receipt.id, binding, purpose))
    factory = SessionUnitOfWorkFactory(sessions)
    identities = await asyncio.gather(
        *[
            complete_verified_account(
                factory, operation_id=operation, binding=binding, purpose=purpose
            )
            for operation, binding, purpose in operations
        ]
    )
    assert identities[0] == identities[1]
    user = await get_user(str(identities[0]))
    assert user is not None and len(user.login_methods) == 1


async def test_resend_cooldown_revokes_previous_code_and_expiry_is_enforced(
    db_session, async_client
):
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    mailbox = Mailbox()
    service = EmailChallengeService(
        sessions, send_email=mailbox.send, enforce_send_limits=allow_test_delivery
    )
    binding = uuid4().hex
    receipt = await service.start(
        email=f"resend-{uuid4().hex}@gmail.com",
        binding=binding,
        purpose="browser_login",
        sender_key=binding,
    )
    old_code = mailbox.code
    with pytest.raises(ChallengeRejected, match="sixty"):
        await service.resend(
            challenge_id=receipt.id,
            binding=binding,
            purpose="browser_login",
            sender_key=binding,
        )
    async with sessions() as session:
        row = await session.get(EmailChallenge, receipt.id)
        row.created_at = datetime.now(timezone.utc) - timedelta(seconds=61)
        await session.commit()
    replacement = await service.resend(
        challenge_id=receipt.id,
        binding=binding,
        purpose="browser_login",
        sender_key=binding,
    )
    with pytest.raises(ChallengeRejected, match="no longer available"):
        await service.verify(
            challenge_id=receipt.id,
            binding=binding,
            purpose="browser_login",
            submitted_code=old_code,
        )
    async with sessions() as session:
        row = await session.get(EmailChallenge, replacement.id)
        row.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
        await session.commit()
    with pytest.raises(ChallengeRejected, match="expired"):
        await service.verify(
            challenge_id=replacement.id,
            binding=binding,
            purpose="browser_login",
            submitted_code=mailbox.code,
        )


async def test_verified_completion_is_bound_and_reuses_the_auth_identity(
    db_session: AsyncSession, async_client: AsyncClient
) -> None:
    assert db_session.bind is not None
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    mailbox = Mailbox()
    service = EmailChallengeService(
        sessions, send_email=mailbox.send, enforce_send_limits=allow_test_delivery
    )
    binding = uuid4().hex
    receipt = await service.start(
        email=f"proof-{uuid4().hex}@gmail.com",
        binding=binding,
        purpose="chat_onboarding",
        sender_key=binding,
    )
    assert len(mailbox.code) == 6 and mailbox.code.isascii() and mailbox.code.isdigit()
    with pytest.raises(ChallengeRejected):
        await service.verify(
            challenge_id=receipt.id,
            binding="another actor",
            purpose="chat_onboarding",
            submitted_code=mailbox.code,
        )
    with pytest.raises(ChallengeRejected):
        await service.verify(
            challenge_id=receipt.id,
            binding=binding,
            purpose="browser_login",
            submitted_code=mailbox.code,
        )
    with pytest.raises(ChallengeRejected, match="six-digit"):
        await service.verify(
            challenge_id=receipt.id,
            binding=binding,
            purpose="chat_onboarding",
            submitted_code="thanks",
        )
    await service.verify(
        challenge_id=receipt.id,
        binding=binding,
        purpose="chat_onboarding",
        submitted_code=mailbox.code,
    )
    factory = SessionUnitOfWorkFactory(sessions)
    ids = await asyncio.gather(
        *[
            complete_verified_account(
                factory,
                operation_id=receipt.id,
                binding=binding,
                purpose="chat_onboarding",
            )
            for _ in range(2)
        ],
        return_exceptions=True,
    )
    assert ids[0] == ids[1] and not isinstance(ids[0], BaseException), ids
    user = await get_user(str(ids[0]))
    assert user is not None
    assert len(user.login_methods) == 1
    assert user.login_methods[0].recipe_id == "passwordless"


async def test_incorrect_codes_have_a_shared_attempt_budget(
    db_session: AsyncSession, async_client: AsyncClient
) -> None:
    assert db_session.bind is not None
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    mailbox = Mailbox()
    service = EmailChallengeService(
        sessions, send_email=mailbox.send, enforce_send_limits=allow_test_delivery
    )
    binding = uuid4().hex
    receipt = await service.start(
        email=f"attempt-{uuid4().hex}@gmail.com",
        binding=binding,
        purpose="browser_login",
        sender_key=binding,
    )
    wrong = "000000" if mailbox.code != "000000" else "111111"
    for _ in range(3):
        with pytest.raises(ChallengeRejected, match="did not match"):
            await service.verify(
                challenge_id=receipt.id,
                binding=binding,
                purpose="browser_login",
                submitted_code=wrong,
            )
    with pytest.raises(ChallengeRejected, match="exhausted"):
        await service.verify(
            challenge_id=receipt.id,
            binding=binding,
            purpose="browser_login",
            submitted_code=mailbox.code,
        )


@pytest.mark.parametrize("recipe", ["emailpassword", "thirdparty", "passwordless"])
async def test_otp_preserves_existing_oss_identity_and_login_method(
    db_session, async_client, recipe
):
    from supertokens_python.recipe.emailpassword.asyncio import sign_up, sign_in
    from supertokens_python.recipe.passwordless.asyncio import signinup
    from supertokens_python.recipe.thirdparty.asyncio import (
        manually_create_or_update_user,
    )
    from supertokens_python.recipe.emailpassword.interfaces import WrongCredentialsError
    from app.modules.identity.infrastructure.models.user_models import User

    email = f"canonical-{uuid4().hex}@gmail.com"
    password = "BeforeVerification@123"
    if recipe == "emailpassword":
        created = await sign_up("public", email, password)
    elif recipe == "thirdparty":
        created = await manually_create_or_update_user(
            "public", "google", uuid4().hex, email, False
        )
    else:
        created = await signinup("public", email=email, phone_number=None)
    canonical_id = created.user.id
    mailbox = Mailbox()
    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    service = EmailChallengeService(
        sessions, send_email=mailbox.send, enforce_send_limits=allow_test_delivery
    )
    binding = uuid4().hex
    proof = await service.start(
        email=email, binding=binding, purpose="chat_onboarding", sender_key=binding
    )
    await service.verify(
        challenge_id=proof.id,
        binding=binding,
        purpose="chat_onboarding",
        submitted_code=mailbox.code,
    )
    user_id = await complete_verified_account(
        SessionUnitOfWorkFactory(sessions),
        operation_id=proof.id,
        binding=binding,
        purpose="chat_onboarding",
    )
    assert str(user_id) == canonical_id
    auth_user = await get_user(canonical_id)
    assert [method.recipe_id for method in auth_user.login_methods] == [recipe]
    async with sessions() as session:
        local = await session.get(User, user_id)
        assert local.is_verified and local.is_active
    if recipe == "emailpassword":
        assert isinstance(
            await sign_in("public", email, password), WrongCredentialsError
        )


async def test_deleted_account_is_not_recreated_after_mailbox_proof(
    db_session, async_client
):
    from app.modules.identity.infrastructure.models.user_models import User

    sessions = async_sessionmaker(db_session.bind, expire_on_commit=False)
    mailbox = Mailbox()
    service = EmailChallengeService(
        sessions, send_email=mailbox.send, enforce_send_limits=allow_test_delivery
    )
    binding = uuid4().hex
    receipt = await service.start(
        email=f"deleted-{uuid4().hex}@gmail.com",
        binding=binding,
        purpose="browser_login",
        sender_key=binding,
    )
    await service.verify(
        challenge_id=receipt.id,
        binding=binding,
        purpose="browser_login",
        submitted_code=mailbox.code,
    )
    kwargs = {
        "operation_id": receipt.id,
        "binding": binding,
        "purpose": "browser_login",
    }
    factory = SessionUnitOfWorkFactory(sessions)
    user_id = await complete_verified_account(factory, **kwargs)
    async with sessions.begin() as session:
        user = await session.get(User, user_id)
        user.is_deleted = True
        user.is_active = False
    with pytest.raises(ChallengeRejected, match="cannot sign in"):
        await complete_verified_account(factory, **kwargs)


async def test_losing_the_email_lease_stops_completion(db_session, async_client):
    import hashlib
    from app.core.infrastructure.redis.client import get_redis
    from app.modules.identity.infrastructure.identity_lease import (
        identity_lease,
        IdentityLeaseLost,
    )

    key = f"lease-loss-{uuid4().hex}"
    with pytest.raises(IdentityLeaseLost):
        async with identity_lease(key) as lease:
            await get_redis().delete(
                f"identity:operation:{hashlib.sha256(key.encode()).hexdigest()}"
            )
            await lease.require_ownership()
