"""Transaction helpers for claiming one profile mobile number at a time."""

from __future__ import annotations

import hashlib
from uuid import UUID

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

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
