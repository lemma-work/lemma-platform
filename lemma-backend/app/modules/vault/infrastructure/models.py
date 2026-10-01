"""The vault's three tables, and the trigger that ties a secret to its owner.

``vault_keys``    key-encryption keys, each wrapped by the root key.
``vault_secrets`` one row per secret: a data key wrapped by a KEK, and the value
                  sealed by that data key. The current version only -- a new
                  value replaces the old one in place, under a new data key.
``vault_secret_events`` who created, changed, revealed or deleted what.

A table that stores secrets here holds a ``*_secret_id`` foreign key and is
declared with :func:`vault_owned`, which installs a trigger: when the owner row
is deleted (by any path -- an ORM delete, a bulk ``DELETE``, a cascade from a
deleted organization, a script) or repoints its column, the secret it held is
deleted in the same transaction. No registry of owners is needed anywhere.
"""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID, uuid7

from sqlalchemy import (
    DDL,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Table,
    Text,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.core.infrastructure.db.base import Base

from app.modules.vault.domain.types import JsonObject


def _now() -> datetime:
    return datetime.now(timezone.utc)


class VaultKey(Base):
    __tablename__ = "vault_keys"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    purpose: Mapped[str] = mapped_column(String(8), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)
    algorithm: Mapped[str] = mapped_column(
        String(16), nullable=False, default="A256GCM"
    )
    root_provider: Mapped[str] = mapped_column(String(32), nullable=False)
    root_key_ref: Mapped[str] = mapped_column(Text, nullable=False)
    wrapped_key: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("purpose IN ('encrypt', 'sign')", name="ck_vault_keys_purpose"),
        CheckConstraint(
            "state IN ('active', 'decrypt_only', 'retired')", name="ck_vault_keys_state"
        ),
        Index(
            "uq_vault_keys_one_active",
            "purpose",
            unique=True,
            postgresql_where=text("state = 'active'"),
        ),
    )


class VaultSecret(Base):
    __tablename__ = "vault_secrets"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    # No foreign keys to the owners' parents: the owner row carries those, and
    # its trigger removes the secret when it goes.
    organization_id: Mapped[UUID | None] = mapped_column()
    pod_id: Mapped[UUID | None] = mapped_column()
    user_id: Mapped[UUID | None] = mapped_column()
    purpose: Mapped[str] = mapped_column(String(120), nullable=False)
    kind: Mapped[str] = mapped_column(String(8), nullable=False)
    owner_table: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str | None] = mapped_column(String(128))
    alg: Mapped[str] = mapped_column(String(16), nullable=False, default="A256GCM")
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kek_id: Mapped[UUID] = mapped_column(ForeignKey("vault_keys.id"), nullable=False)
    wrapped_dek: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Non-secret only: nothing here is encrypted.
    metadata_: Mapped[JsonObject] = mapped_column(
        "metadata", JSONB, nullable=False, default=dict
    )
    lease_holder: Mapped[str | None] = mapped_column(String(64))
    lease_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        CheckConstraint("kind IN ('text', 'json')", name="ck_vault_secrets_kind"),
        Index("ix_vault_secrets_kek_id", "kek_id"),
        Index(
            "ix_vault_secrets_organization_id",
            "organization_id",
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
    )


class VaultSecretEvent(Base):
    __tablename__ = "vault_secret_events"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid7)
    # Not a foreign key: the record of a deletion outlives the secret.
    secret_id: Mapped[UUID] = mapped_column(nullable=False)
    organization_id: Mapped[UUID | None] = mapped_column()
    purpose: Mapped[str | None] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_kind: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str | None] = mapped_column(String(160))
    version: Mapped[int | None] = mapped_column(Integer)
    request_id: Mapped[str | None] = mapped_column(String(160))
    detail: Mapped[JsonObject] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_now
    )

    __table_args__ = (
        Index("ix_vault_secret_events_secret", "secret_id", "occurred_at"),
        Index(
            "ix_vault_secret_events_org",
            "organization_id",
            "occurred_at",
            postgresql_where=text("organization_id IS NOT NULL"),
        ),
    )


#: Installed with ``vault_secrets``. Takes the owner's secret-id column names as
#: trigger arguments, so one function serves every owner table.
RELEASE_OWNED_SECRETS_FUNCTION = """
CREATE OR REPLACE FUNCTION vault_release_owned_secrets() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  col text;
  old_id uuid;
  new_id uuid;
BEGIN
  FOREACH col IN ARRAY TG_ARGV LOOP
    EXECUTE format('SELECT ($1).%I', col) USING OLD INTO old_id;
    new_id := NULL;
    IF TG_OP = 'UPDATE' THEN
      EXECUTE format('SELECT ($1).%I', col) USING NEW INTO new_id;
    END IF;
    IF old_id IS NOT NULL AND old_id IS DISTINCT FROM new_id THEN
      DELETE FROM vault_secrets WHERE id = old_id;
      IF FOUND THEN
        INSERT INTO vault_secret_events (id, secret_id, action, actor_kind, detail, occurred_at)
        VALUES (gen_random_uuid(), old_id, 'deleted', 'owner',
                jsonb_build_object('table', TG_TABLE_NAME, 'op', TG_OP), now());
      END IF;
    END IF;
  END LOOP;
  RETURN NULL;
END $$;
"""


def owner_trigger_sql(table_name: str, columns: tuple[str, ...]) -> str:
    """The ``CREATE TRIGGER`` for one owner table. Shared with the migration."""
    cols = ", ".join(columns)
    args = ", ".join(f"'{column}'" for column in columns)
    return (
        f"CREATE TRIGGER vault_release_{table_name} "
        f"AFTER DELETE OR UPDATE OF {cols} ON {table_name} "
        f"FOR EACH ROW EXECUTE FUNCTION vault_release_owned_secrets({args})"
    )


# `DDL` formats its statement with `%`, so the function's own `%I` is escaped.
event.listen(
    VaultSecret.__table__,
    "after_create",
    DDL(RELEASE_OWNED_SECRETS_FUNCTION.replace("%", "%%")),
)


def vault_owned(table: Table, *columns: str) -> None:
    """Declare ``columns`` of ``table`` as references to secrets it owns.

    Installs the release trigger whenever the table is created from metadata
    (the e2e schema) -- the migration installs the identical SQL. Records the
    columns in ``table.info`` for the check that every secret reference has one.
    """
    table.info["vault_owned_columns"] = columns
    event.listen(table, "after_create", DDL(owner_trigger_sql(table.name, columns)))
