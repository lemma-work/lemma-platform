"""Bound email proof with short database stages and resumable completion."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import select

from app.core.infrastructure.db.uow_factory import AsyncSessionMaker
from app.modules.identity.domain.email import normalize_identity_email
from app.modules.identity.domain.email_challenge import (
    MAX_CODE_ATTEMPTS,
    PENDING_TTL_SECONDS,
    RESEND_COOLDOWN_SECONDS,
    parse_code_reply,
)
from app.modules.identity.infrastructure.identity_lease import identity_lease
from app.modules.identity.infrastructure.models.email_challenge_models import (
    EmailChallenge,
)
from app.modules.identity.infrastructure.supertokens_auth.passwordless_challenges import (
    check_email_challenge,
    issue_email_challenge,
    revoke_email_challenge,
)

ChallengePurpose = Literal["browser_login", "chat_onboarding"]


class ChallengeRejected(ValueError):
    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class ChallengeEmailSender(Protocol):
    async def __call__(self, *, email: str, code: str) -> bool: ...


class ChallengeSendLimits(Protocol):
    async def __call__(self, *, email: str, sender_key: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ChallengeReceipt:
    id: UUID
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class VerifiedEmailOperation:
    id: UUID
    email: str
    completed_user_id: UUID | None


def _binding_hash(binding: str, purpose: ChallengePurpose) -> str:
    if not binding:
        raise ChallengeRejected("Verification requires an initiating identity")
    return hashlib.sha256(f"{purpose}\0{binding}".encode()).hexdigest()


class EmailChallengeService:
    def __init__(
        self,
        sessions: AsyncSessionMaker,
        *,
        send_email: ChallengeEmailSender,
        enforce_send_limits: ChallengeSendLimits,
    ) -> None:
        self._sessions = sessions
        self._send_email = send_email
        self._enforce_send_limits = enforce_send_limits

    async def start(
        self,
        *,
        email: str,
        binding: str,
        purpose: ChallengePurpose,
        sender_key: str,
    ) -> ChallengeReceipt:
        email = normalize_identity_email(email)
        digest = _binding_hash(binding, purpose)
        async with identity_lease(f"challenge:{digest}") as lease:
            now = datetime.now(timezone.utc)
            async with self._sessions() as session:
                previous = list(
                    (
                        await session.scalars(
                            select(EmailChallenge).where(
                                EmailChallenge.binding_hash == digest,
                                EmailChallenge.purpose == purpose,
                                EmailChallenge.revoked_at.is_(None),
                            )
                        )
                    ).all()
                )
                if any(
                    row.created_at + timedelta(seconds=RESEND_COOLDOWN_SECONDS) > now
                    for row in previous
                ):
                    raise ChallengeRejected(
                        "Wait sixty seconds before requesting another code"
                    )
                old_code_ids = [row.code_id for row in previous]
                for row in previous:
                    row.revoked_at = now
                await session.commit()
            for code_id in old_code_ids:
                await revoke_email_challenge(code_id)
            await self._enforce_send_limits(email=email, sender_key=sender_key)
            await lease.require_ownership()
            provider = await issue_email_challenge(email)
            await lease.require_ownership()
            expires_at = datetime.fromtimestamp(
                provider.expires_at_ms / 1000, timezone.utc
            )
            async with self._sessions() as session:
                row = EmailChallenge(
                    email=email,
                    purpose=purpose,
                    binding_hash=digest,
                    pre_auth_session_id=provider.pre_auth_session_id,
                    code_id=provider.code_id,
                    device_id=provider.device_id,
                    expires_at=expires_at,
                )
                session.add(row)
                await session.flush()
                receipt = ChallengeReceipt(row.id, expires_at)
                await session.commit()
            if not await self._send_email(email=email, code=provider.code):
                await self.cancel(
                    challenge_id=receipt.id, binding=binding, purpose=purpose
                )
                raise ChallengeRejected("The code could not be delivered; retry")
            await lease.require_ownership()
            return receipt

    async def resend(
        self,
        *,
        challenge_id: UUID,
        binding: str,
        purpose: ChallengePurpose,
        sender_key: str,
    ) -> ChallengeReceipt:
        digest = _binding_hash(binding, purpose)
        async with self._sessions() as session:
            row = await session.get(EmailChallenge, challenge_id)
            self._require_bound(row, digest, purpose)
            assert row is not None
            if row.verified_at is not None:
                raise ChallengeRejected("Verification is already complete")
            email = row.email
        return await self.start(
            email=email, binding=binding, purpose=purpose, sender_key=sender_key
        )

    async def verify(
        self,
        *,
        challenge_id: UUID,
        binding: str,
        purpose: ChallengePurpose,
        submitted_code: str,
    ) -> VerifiedEmailOperation:
        digest = _binding_hash(binding, purpose)
        async with identity_lease(f"challenge:{digest}") as lease:
            async with self._sessions() as session:
                row = await session.get(
                    EmailChallenge, challenge_id, with_for_update=True
                )
                self._require_bound(row, digest, purpose)
                assert row is not None
                now = datetime.now(timezone.utc)
                if row.verified_at is not None:
                    if row.verified_at + timedelta(seconds=PENDING_TTL_SECONDS) <= now:
                        raise ChallengeRejected(
                            "Verification expired; request another code"
                        )
                    operation = VerifiedEmailOperation(
                        row.id, row.email, row.completed_user_id
                    )
                    code_id = row.code_id
                    verified = True
                    pre_auth_session_id = device_id = code = ""
                else:
                    code = parse_code_reply(submitted_code)
                    if code is None:
                        raise ChallengeRejected(
                            "Enter the six-digit code from your email"
                        )
                    if row.expires_at <= now or row.attempts >= MAX_CODE_ATTEMPTS:
                        raise ChallengeRejected(
                            "Code expired or attempts exhausted; request another code"
                        )
                    row.attempts += 1
                    pre_auth_session_id, device_id = (
                        row.pre_auth_session_id,
                        row.device_id,
                    )
                    code_id = row.code_id
                    operation = VerifiedEmailOperation(
                        row.id, row.email, row.completed_user_id
                    )
                    verified = False
                await session.commit()
            if not verified:
                accepted = await check_email_challenge(
                    pre_auth_session_id=pre_auth_session_id,
                    device_id=device_id,
                    code=code,
                )
                await lease.require_ownership()
                if not accepted:
                    raise ChallengeRejected("The code did not match; try again")
                async with self._sessions() as session:
                    row = await session.get(
                        EmailChallenge, challenge_id, with_for_update=True
                    )
                    self._require_bound(row, digest, purpose)
                    assert row is not None
                    if row.expires_at <= datetime.now(timezone.utc):
                        raise ChallengeRejected("Code expired; request another code")
                    row.verified_at = datetime.now(timezone.utc)
                    await session.commit()
            # Verification is durable before revocation. A failed revocation is
            # retried from the recorded operation without checking the code again.
            await revoke_email_challenge(code_id)
            await lease.require_ownership()
            return operation

    async def cancel(
        self, *, challenge_id: UUID, binding: str, purpose: ChallengePurpose
    ) -> None:
        digest = _binding_hash(binding, purpose)
        async with self._sessions() as session:
            row = await session.get(EmailChallenge, challenge_id, with_for_update=True)
            self._require_bound(row, digest, purpose)
            assert row is not None
            row.revoked_at = datetime.now(timezone.utc)
            code_id = row.code_id
            await session.commit()
        await revoke_email_challenge(code_id)

    @staticmethod
    def _require_bound(
        row: EmailChallenge | None, digest: str, purpose: ChallengePurpose
    ) -> None:
        if (
            row is None
            or row.purpose != purpose
            or not hmac.compare_digest(row.binding_hash, digest)
            or row.revoked_at is not None
        ):
            raise ChallengeRejected("Verification is no longer available; start again")
