"""Moving data keys onto the active KEK, so an old KEK can be retired.

Only the 60-byte wrapped data key changes; each value's ciphertext is left as
it is and no plaintext is produced. Batches take ``FOR UPDATE SKIP LOCKED`` and
commit, so this can run next to live traffic and on several processes at once.
All crypto is local -- no root-key call -- so holding the session is fine.
"""

from __future__ import annotations

from collections.abc import Callable
from uuid import UUID

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.log.log import get_logger

from app.modules.vault.domain.errors import VaultUnavailable
from app.modules.vault.domain.types import ActorKind, AuditActor
from app.modules.vault.infrastructure.models import VaultSecret
from app.modules.vault.services.audit import SecretAction, record_event
from app.modules.vault.services.envelope import rewrap_dek
from app.modules.vault.services.keyring import VaultKeyring

logger = get_logger(__name__)


async def rewrap_batch(
    session: AsyncSession, keyring: VaultKeyring, *, batch_size: int
) -> int:
    """Rewrap up to ``batch_size`` secrets not on the active KEK. Returns the count.

    Reads keys from memory only; the caller loads them before opening the session.
    """
    cached = keyring.cached_active_encryption_key()
    if cached is None:
        raise VaultUnavailable("vault keys are not loaded")
    active_id, active = cached
    rows = (
        await session.execute(
            select(VaultSecret.id, VaultSecret.kek_id, VaultSecret.wrapped_dek)
            .where(VaultSecret.kek_id != active_id)
            .order_by(VaultSecret.id)
            .limit(batch_size)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for secret_id, kek_id, wrapped in rows:
        old = keyring.cached_encryption_key(kek_id)
        if old is None:
            raise VaultUnavailable(f"vault key {kek_id} is retired or missing")
        await session.execute(
            update(VaultSecret)
            .where(VaultSecret.id == secret_id)
            .values(
                kek_id=active_id,
                wrapped_dek=rewrap_dek(
                    secret_id=secret_id,
                    old_kek_id=kek_id,
                    old_kek=old,
                    new_kek_id=active_id,
                    new_kek=active,
                    wrapped_dek=wrapped,
                ),
            )
        )
    if rows:
        await record_event(
            session,
            secret_id=rows[0][0],
            organization_id=None,
            purpose=None,
            action=SecretAction.REWRAPPED,
            actor=AuditActor(ActorKind.JOB, "rewrap"),
            version=None,
            detail={"count": len(rows), "to_kek": str(active_id)},
        )
    return len(rows)


async def rewrap_all(
    session_factory: Callable[[], AsyncSession],
    keyring: VaultKeyring,
    *,
    batch_size: int,
) -> int:
    await keyring.load()
    total = 0
    while True:
        async with session_factory() as session:
            moved = await rewrap_batch(session, keyring, batch_size=batch_size)
            await session.commit()
        total += moved
        if moved:
            logger.info("vault.rewrap.batch_completed", rewrapped_count=moved)
        if moved < batch_size:
            return total


async def secrets_per_key(session: AsyncSession) -> dict[UUID, int]:
    rows = await session.execute(
        select(VaultSecret.kek_id, func.count()).group_by(VaultSecret.kek_id)
    )
    return {kek_id: int(count) for kek_id, count in rows.all()}
