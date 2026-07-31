"""Re-encrypt secrets forward onto the current primary key.

Drives key rotation and key-compromise recovery: walk every encrypted column,
decrypt with whatever key/version produced each value, and re-encrypt under the
current primary. Idempotent — rows already under the primary key are skipped
(unless ``force`` is set, e.g. to re-wrap KMS DEKs under a freshly rotated KEK
version).

The registry is the single source of truth for *what* is encrypted at rest.
Reusable from a CLI (``scripts/reencrypt_secrets.py``), an admin endpoint, or a
background job — all call :func:`reencrypt_all`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto.ports import SecretCipher
from app.core.log.log import get_logger

logger = get_logger(__name__)


@dataclass(frozen=True)
class EncryptedColumn:
    """A column holding secrets at rest."""

    table: str
    column: str
    kind: str  # "json" (JSONB envelope) | "str" (compact string envelope)
    label: str

    @property
    def value_expr(self) -> str:
        """RHS for the UPDATE assignment (``CAST(:val AS jsonb)`` avoids the
        ``:val::jsonb`` ambiguity in SQLAlchemy ``text()``)."""
        return "CAST(:val AS jsonb)" if self.kind == "json" else ":val"


#: Every encrypted column. Phase 4 appends agent_surfaces.webhook_secret once
#: that column is widened and the repository encrypts/decrypts it.
REGISTRY: list[EncryptedColumn] = [
    EncryptedColumn("accounts", "credentials", "json", "accounts.credentials"),
    EncryptedColumn(
        "auth_configs", "provider_config", "json", "auth_configs.provider_config"
    ),
    EncryptedColumn(
        "agent_runtime_profiles",
        "credentials",
        "json",
        "agent_runtime_profiles.credentials",
    ),
    EncryptedColumn(
        "agent_surfaces", "webhook_secret", "str", "agent_surfaces.webhook_secret"
    ),
]


def _reencrypt_value(
    cipher: SecretCipher, col: EncryptedColumn, raw_text: str, *, force: bool
) -> str | None:
    """Return the new stored text, or ``None`` to leave the row unchanged."""
    if col.kind == "json":
        value = json.loads(raw_text)
        if value is None:
            return None
        if not force and cipher.is_under_primary_key(value):
            return None
        reencrypted = cipher.encrypt_json(cipher.decrypt_json(value))
        return json.dumps(reencrypted)

    # string column (may be plaintext on first backfill)
    value = raw_text
    if not value:
        return None
    if not force and cipher.is_under_primary_key(value):
        return None
    return cipher.encrypt_str(cipher.decrypt_str(value))


async def reencrypt_column(
    session: AsyncSession,
    cipher: SecretCipher,
    col: EncryptedColumn,
    *,
    force: bool = False,
    batch_size: int = 500,
    dry_run: bool = False,
) -> dict[str, int]:
    scanned = 0
    migrated = 0
    last_id = None
    select_sql = f"SELECT id, {col.column}::text AS v FROM {col.table}"
    update_sql = text(
        f"UPDATE {col.table} SET {col.column} = {col.value_expr} WHERE id = :id"
    )

    while True:
        if last_id is None:
            query = text(f"{select_sql} ORDER BY id LIMIT :lim")
            params: dict[str, object] = {"lim": batch_size}
        else:
            query = text(f"{select_sql} WHERE id > :last ORDER BY id LIMIT :lim")
            params = {"lim": batch_size, "last": last_id}

        rows = (await session.execute(query, params)).all()
        if not rows:
            break

        for row_id, raw_text in rows:
            last_id = row_id
            scanned += 1
            if raw_text is None:
                continue
            new_text = _reencrypt_value(cipher, col, raw_text, force=force)
            if new_text is None:
                continue
            migrated += 1
            if not dry_run:
                await session.execute(update_sql, {"val": new_text, "id": row_id})

        if not dry_run:
            await session.commit()

    return {"scanned": scanned, "migrated": migrated}


async def reencrypt_all(
    session: AsyncSession,
    cipher: SecretCipher,
    *,
    force: bool = False,
    batch_size: int = 500,
    dry_run: bool = False,
    columns: list[EncryptedColumn] | None = None,
) -> dict[str, dict[str, int]]:
    """Re-encrypt every registered column; return ``{label: {scanned, migrated}}``."""
    report: dict[str, dict[str, int]] = {}
    for col in columns or REGISTRY:
        result = await reencrypt_column(
            session, cipher, col, force=force, batch_size=batch_size, dry_run=dry_run
        )
        report[col.label] = result
        logger.debug("crypto.rotation.reencrypt_s_scanned_d_migrated.observed")
    return report
