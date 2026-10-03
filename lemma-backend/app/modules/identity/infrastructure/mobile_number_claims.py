"""Transaction helpers for claiming one profile mobile number at a time."""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.transaction_locks import (
    mark_transaction_scoped_lock,
)
from app.modules.identity.infrastructure.models import User


def _claim_lock_key(digits: str) -> int:
    """Return a stable signed 64-bit lock key without exposing the number."""
    digest = hashlib.blake2b(
        digits.encode("ascii"), digest_size=8, person=b"lemma-mobile"
    ).digest()
    return int.from_bytes(digest, byteorder="big", signed=True)


async def acquire_mobile_number_claim_lock(session: AsyncSession, digits: str) -> None:
    """Serialize claims for normalized digits until this transaction ends."""
    if not digits or not digits.isdigit():
        raise ValueError("Normalized mobile digits are required")
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:lock_key)"),
        {"lock_key": _claim_lock_key(digits)},
    )
    # Held on a Session, so a connection-scope release could commit it and drop
    # the lock mid-claim. The two other advisory locks in the codebase are taken
    # on a raw connection inside `engine.begin()`, which no release helper can
    # reach, so only this one needs the mark.
    mark_transaction_scoped_lock(session)


async def get_other_mobile_number_owner_id(
    session: AsyncSession,
    *,
    digits: str,
    user_id: UUID,
) -> UUID | None:
    """Return another profile holding these normalized digits, if one exists."""
    return await session.scalar(
        select(User.id).where(
            User.mobile_number.isnot(None),
            func.regexp_replace(User.mobile_number, r"\D", "", "g") == digits,
            User.id != user_id,
        )
    )


def _holds_digits(digits: str):
    return (
        User.mobile_number.isnot(None),
        func.regexp_replace(User.mobile_number, r"\D", "", "g") == digits,
    )


async def release_stale_mobile_number_claims(
    session: AsyncSession, *, digits: str, user_id: UUID
) -> None:
    """Take these digits off accounts that can no longer use them.

    A deleted or deactivated account kept its number, and every proof-based
    claim treated it as an owner: the person now holding the SIM proved it on
    WhatsApp or Telegram and was told the number "belongs to another account" --
    an account nobody can sign in to, so there was nothing anyone could do about
    it. Nobody is served by that claim, so it is released here, under the claim
    lock the caller already holds, rather than by a sweep that could race a
    reactivation.

    The verification stamp goes with the number: a verified number is what the
    `uq_users_verified_mobile_e164` index holds, and leaving the stamp on a NULL
    number would claim to have verified nothing.
    """
    await session.execute(
        update(User)
        .where(
            *_holds_digits(digits),
            User.id != user_id,
            or_(User.is_deleted.is_(True), User.is_active.is_(False)),
        )
        .values(mobile_number=None, mobile_verified_at=None)
        .execution_options(synchronize_session=False)
    )


async def get_live_mobile_number_owner_id(
    session: AsyncSession,
    *,
    digits: str,
    user_id: UUID,
) -> UUID | None:
    """Another *live, verified* holder of these digits, if one exists.

    The question a proof-based claim asks. The person in front of us has just
    proved the number -- a WhatsApp message from it, a Telegram contact share --
    so the only holder whose claim is stronger is one who proved it too and can
    still use the account. A number somebody merely typed into a profile, or an
    account that has gone, is not an owner; `get_other_mobile_number_owner_id`
    stays the stricter check for the paths with no proof behind them.
    """
    return await session.scalar(
        select(User.id).where(
            *_holds_digits(digits),
            User.id != user_id,
            User.mobile_verified_at.isnot(None),
            User.is_active.is_(True),
            User.is_deleted.is_(False),
        )
    )


async def proven_claim_blocker(
    session: AsyncSession, *, digits: str, user_id: UUID
) -> UUID | None:
    """Release stale claims, then name whoever still blocks a proven one.

    Both halves in one call so no caller can do the second without the first;
    the caller must already hold `acquire_mobile_number_claim_lock`.
    """
    await release_stale_mobile_number_claims(session, digits=digits, user_id=user_id)
    return await get_live_mobile_number_owner_id(
        session, digits=digits, user_id=user_id
    )
