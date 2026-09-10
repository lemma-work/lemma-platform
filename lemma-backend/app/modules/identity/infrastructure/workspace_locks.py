from __future__ import annotations

import hashlib

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.infrastructure.db.transaction_locks import mark_transaction_scoped_lock


async def lock_workspace_selection(session: AsyncSession, key: str) -> None:
    digest = hashlib.blake2b(
        key.encode(), digest_size=8, person=b"lemma-workspace"
    ).digest()
    await session.execute(
        text("SELECT pg_advisory_xact_lock(:key)"),
        {"key": int.from_bytes(digest, "big", signed=True)},
    )
    mark_transaction_scoped_lock(session)
