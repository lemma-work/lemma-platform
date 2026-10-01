"""Move every stored secret into the vault, in one step.

Secrets were encrypted column by column with Fernet -- account credentials,
install configs, model-provider keys, surface webhook secrets, the WhatsApp
pool -- with no associated data, so a ciphertext copied from one tenant's row
into another's decrypted for the other. They now live in ``vault_secrets``,
each under its own data key (AES-256-GCM) wrapped by a key-encryption key in
``vault_keys``, which is wrapped by the root key (Cloud KMS, or the configured
keyset) that never enters the database. Each value is bound to its owner's
scope and to its purpose.

One transaction, no interim period in which both layouts exist (the vault's
tables and trigger function came in 0043):

1. add a ``*_secret_id`` reference to each owner;
2. read every old value one last time -- Fernet ``fernet-json-v1``,
   ``lemma-secret-v2`` under ``fernet`` or ``kms+fernet``, the ``lsenc1:``
   string form, and plaintext left from before encryption -- and write it to
   the vault;
3. drop the old columns, and install the per-owner release triggers.

Runtime-profile header values, which were stored in plaintext inside
``config``, move into the vault with the profile's credentials. Queued
agent-host commands have their MCP frame re-sealed in place.

Requirements: the process running this needs the same key configuration as the
backend -- ``SECRET_ENCRYPTION_KEY(SET)`` to read the old rows, and the root
(``GCP_KMS_KEY_NAME`` with ``roles/cloudkms.cryptoKeyEncrypterDecrypter``, or
the keyset) to create the first key-encryption key. It is only needed when
there are secrets to move; an empty installation never calls the root. Any
failure rolls the whole migration back, so it is safe to fix and re-run.

The old decoders below are frozen copies of the retired ``app/core/crypto``
cipher, kept here so this file reads exactly what was written.

Downgrade restores the old schema only on an installation holding no secrets;
with secrets present it refuses, because the old encrypted form cannot be
rebuilt here. Restore the pre-upgrade backup instead.

Revision ID: 0044_vault_cutover
Revises: 0043_vault_tables
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy.dialects import postgresql

revision = "0044_vault_cutover"
down_revision = "0043_vault_tables"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")

#: owner table, secret-id column(s)
_OWNERS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("accounts", ("credentials_secret_id",)),
    ("auth_configs", ("config_secret_id",)),
    ("agent_runtime_profiles", ("secrets_secret_id",)),
    ("agent_surfaces", ("webhook_secret_id",)),
    ("surface_whatsapp_numbers", ("credentials_secret_id",)),
)


# --------------------------------------------------------------------------
# Frozen legacy decoding (the retired app/core/crypto cipher, read side only)
# --------------------------------------------------------------------------
_V2 = "lemma-secret-v2"
_V1 = "fernet-json-v1"
_STR_PREFIX = "lsenc1:"


class _Legacy:
    def __init__(self) -> None:
        from app.core.config import settings
        from app.core.crypto.keys import legacy_candidate_secrets, load_static_keyring
        from app.core.crypto.roots.factory import get_root_key_provider
        from app.core.crypto.roots.local import LocalKeyringRoot

        root = get_root_key_provider()
        keys: dict[str, bytes] = {}
        if isinstance(root, LocalKeyringRoot):
            keys = {kid: m.secret for kid, m in root.keyring().keys.items()}
        else:
            try:
                keys = {kid: m.secret for kid, m in load_static_keyring().keys.items()}
            except RuntimeError:
                keys = {}
        self._by_kid = {kid: Fernet(secret) for kid, secret in keys.items()}
        candidates = list(legacy_candidate_secrets())
        candidates += [s for s in keys.values() if s not in candidates]
        self._any = MultiFernet([Fernet(s) for s in candidates]) if candidates else None
        self._kms_key = settings.gcp_kms_key_name
        self._kms = None

    def _unwrap_kms(self, wrapped: bytes) -> bytes:
        if self._kms is None:
            from app.core.crypto.config import crypto_settings
            from app.core.crypto.roots.kms_client import google_cloud_kms

            if not self._kms_key:
                raise RuntimeError(
                    "A kms+fernet secret needs GCP_KMS_KEY_NAME to be read"
                )
            self._kms = google_cloud_kms(
                timeout_seconds=crypto_settings.gcp_kms_timeout_seconds,
                max_attempts=crypto_settings.gcp_kms_max_attempts,
            )
        return self._kms.decrypt(self._kms_key, wrapped, b"")

    def _v2(self, envelope: dict) -> bytes:
        ciphertext = base64.urlsafe_b64decode(envelope["ct"].encode("ascii"))
        alg = envelope.get("alg")
        if alg == "kms+fernet":
            dek = self._unwrap_kms(
                base64.urlsafe_b64decode(envelope["dek"].encode("ascii"))
            )
            return Fernet(dek).decrypt(ciphertext)
        if alg == "fernet":
            fernet = self._by_kid.get(str(envelope.get("kid")))
            if fernet is not None:
                try:
                    return fernet.decrypt(ciphertext)
                except InvalidToken:
                    pass
            if self._any is not None:
                return self._any.decrypt(ciphertext)
            raise RuntimeError(
                f"No key to decrypt a secret under kid {envelope.get('kid')!r}"
            )
        raise RuntimeError(f"Unknown secret envelope alg {alg!r}")

    def json(self, value: object) -> dict | None:
        if value is None:
            return None
        if isinstance(value, str):
            value = json.loads(value)
        if value is None:  # a JSON null
            return None
        if not isinstance(value, dict):
            raise RuntimeError("A stored JSON secret is not an object")
        marker = value.get("_encrypted")
        if marker == _V2:
            payload = self._v2(value)
        elif marker == _V1:
            if self._any is None:
                raise RuntimeError(
                    "A fernet-json-v1 secret needs its old key configured"
                )
            payload = self._any.decrypt(str(value["ciphertext"]).encode("ascii"))
        else:
            return value  # plaintext from before encryption at rest
        decoded = json.loads(payload.decode("utf-8"))
        if not isinstance(decoded, dict):
            raise RuntimeError("A stored JSON secret did not decode to an object")
        return decoded

    def text(self, value: str | None) -> str | None:
        if not value:
            return None
        if not value.startswith(_STR_PREFIX):
            return value  # plaintext from before encryption at rest
        raw = base64.urlsafe_b64decode(value[len(_STR_PREFIX) :].encode("ascii"))
        return self._v2(json.loads(raw.decode("utf-8"))).decode("utf-8")


# --------------------------------------------------------------------------
# Frozen vault format (app/modules/vault/services/envelope.py and sealer.py)
# --------------------------------------------------------------------------
def _aad(*parts: str) -> bytes:
    out = bytearray()
    for part in parts:
        raw = part.encode("utf-8")
        out += len(raw).to_bytes(4, "big") + raw
    return bytes(out)


def _seal(key: bytes, plaintext: bytes, aad: bytes) -> bytes:
    nonce = os.urandom(12)
    return nonce + AESGCM(key).encrypt(nonce, plaintext, aad)


def _run(coro):
    # The migration runs inside Alembic's event loop (a greenlet on it), where
    # asyncio.run cannot be called; the root's coroutine gets a loop of its own.
    with ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()


class _Vault:
    """Just enough of the vault to write secrets in its format."""

    def __init__(self, bind: sa.Connection) -> None:
        self._bind = bind
        self._kek: tuple[uuid.UUID, bytes] | None = None
        self.count = 0

    def _key(self) -> tuple[uuid.UUID, bytes]:
        if self._kek is None:
            from app.core.crypto.roots.factory import (
                get_root_key_provider,
                validate_root_key_config,
            )

            validate_root_key_config()
            root = get_root_key_provider()
            kek_id = uuid.uuid7()
            kek = os.urandom(32)
            wrapped = _run(
                root.wrap(kek, aad=_aad("lemma-vault/kek/v1", str(kek_id), "encrypt"))
            )
            self._bind.execute(
                sa.text(
                    "INSERT INTO vault_keys (id, purpose, state, algorithm, root_provider, "
                    "root_key_ref, wrapped_key, created_at) VALUES (:id, 'encrypt', "
                    "'active', 'A256GCM', :provider, :ref, :blob, now())"
                ),
                {
                    "id": kek_id,
                    "provider": root.name,
                    "ref": wrapped.root_ref,
                    "blob": wrapped.blob,
                },
            )
            self._kek = (kek_id, kek)
        return self._kek

    def put(
        self,
        *,
        value: str | dict,
        purpose: str,
        owner_table: str,
        org: uuid.UUID | None,
        pod: uuid.UUID | None = None,
        user: uuid.UUID | None = None,
        expires_at: datetime | None = None,
    ) -> uuid.UUID:
        kek_id, kek = self._key()
        secret_id = uuid.uuid7()
        kind = "text" if isinstance(value, str) else "json"
        payload = (
            value.encode("utf-8")
            if isinstance(value, str)
            else json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
                "utf-8"
            )
        )
        dek = os.urandom(32)
        wrapped = _seal(
            kek, dek, _aad("lemma-vault/dek/v1", str(secret_id), str(kek_id))
        )
        sealed = _seal(
            dek,
            payload,
            _aad(
                "lemma-vault/secret/v1",
                str(secret_id),
                "1",
                kind,
                purpose,
                "" if org is None else str(org),
                "" if pod is None else str(pod),
                "" if user is None else str(user),
            ),
        )
        self._bind.execute(
            sa.text(
                "INSERT INTO vault_secrets (id, organization_id, pod_id, user_id, purpose, "
                "kind, owner_table, alg, version, kek_id, wrapped_dek, ciphertext, "
                "expires_at, metadata, created_at, updated_at) VALUES (:id, :org, :pod, "
                ":user, :purpose, :kind, :owner, 'A256GCM', 1, :kek, :wrapped, :sealed, "
                ":expires, '{}'::jsonb, now(), now())"
            ),
            {
                "id": secret_id,
                "org": org,
                "pod": pod,
                "user": user,
                "purpose": purpose,
                "kind": kind,
                "owner": owner_table,
                "kek": kek_id,
                "wrapped": wrapped,
                "sealed": sealed,
                "expires": expires_at,
            },
        )
        self._bind.execute(
            sa.text(
                "INSERT INTO vault_secret_events (id, secret_id, organization_id, purpose, "
                "action, actor_kind, version, detail, occurred_at) VALUES "
                "(:id, :secret, :org, :purpose, 'imported', 'job', 1, '{}'::jsonb, now())"
            ),
            {"id": uuid.uuid7(), "secret": secret_id, "org": org, "purpose": purpose},
        )
        self.count += 1
        return secret_id

    def seal_transient(self, value: dict, *, purpose: str, bindings: list[str]) -> str:
        kek_id, kek = self._key()
        dek = os.urandom(32)
        wrapped = _seal(kek, dek, _aad("lemma-vault/sealed-dek/v1", str(kek_id)))
        payload = json.dumps(value, sort_keys=True, separators=(",", ":")).encode(
            "utf-8"
        )
        sealed = _seal(
            dek, payload, _aad("lemma-vault/sealed/v1", purpose, "json", *bindings)
        )
        raw = kek_id.bytes + wrapped + b"j" + sealed
        return "lvs1:" + base64.urlsafe_b64encode(raw).decode("ascii")


def _expiry(credentials: dict) -> datetime | None:
    raw = credentials.get("expires_at") or credentials.get("token_expires_at")
    if isinstance(raw, bool) or raw is None:
        return None
    if isinstance(raw, (int, float)):
        seconds = raw / 1000 if raw > 10**12 else raw
        return datetime.fromtimestamp(seconds, tz=timezone.utc)
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


# --------------------------------------------------------------------------
# Upgrade
# --------------------------------------------------------------------------
def _rows(bind: sa.Connection, sql: str) -> list[sa.Row]:
    return list(bind.execute(sa.text(sql)).all())


def _link(
    bind: sa.Connection, table: str, column: str, row_id: object, secret_id: uuid.UUID
) -> None:
    bind.execute(
        sa.text(f"UPDATE {table} SET {column} = :secret WHERE id = :id"),
        {"secret": secret_id, "id": row_id},
    )


def _move_secrets(bind: sa.Connection) -> None:
    legacy = _Legacy()
    vault = _Vault(bind)

    for row_id, org, user, raw in _rows(
        bind,
        "SELECT id, organization_id, user_id, credentials FROM accounts "
        "WHERE credentials IS NOT NULL",
    ):
        creds = legacy.json(raw)
        if creds is not None:
            secret = vault.put(
                value=creds,
                purpose="connectors.account.credentials",
                owner_table="accounts",
                org=org,
                user=user,
                expires_at=_expiry(creds),
            )
            _link(bind, "accounts", "credentials_secret_id", row_id, secret)

    for row_id, org, raw in _rows(
        bind,
        "SELECT id, organization_id, config FROM auth_configs WHERE config IS NOT NULL",
    ):
        config = legacy.json(raw)
        if config:
            secret = vault.put(
                value=config,
                purpose="connectors.auth_config.config",
                owner_table="auth_configs",
                org=org,
            )
            _link(bind, "auth_configs", "config_secret_id", row_id, secret)

    for row_id, org, raw_creds, raw_config in _rows(
        bind,
        "SELECT id, organization_id, credentials, config FROM agent_runtime_profiles",
    ):
        config = (
            json.loads(raw_config)
            if isinstance(raw_config, str)
            else (raw_config or {})
        )
        headers = config.get("headers") if isinstance(config, dict) else None
        creds = legacy.json(raw_creds)
        if creds or headers:
            secret = vault.put(
                value={"credentials": creds, "headers": headers or {}},
                purpose="agent.runtime_profile.secrets",
                owner_table="agent_runtime_profiles",
                org=org,
            )
            _link(bind, "agent_runtime_profiles", "secrets_secret_id", row_id, secret)
    op.execute(
        "UPDATE agent_runtime_profiles SET config = config - 'headers' "
        "WHERE config -> 'headers' IS NOT NULL"
    )

    for row_id, org, pod, raw in _rows(
        bind,
        "SELECT id, organization_id, pod_id, webhook_secret FROM agent_surfaces "
        "WHERE webhook_secret IS NOT NULL AND webhook_secret <> ''",
    ):
        value = legacy.text(raw)
        if value:
            secret = vault.put(
                value=value,
                purpose="agent_surfaces.surface.webhook_secret",
                owner_table="agent_surfaces",
                org=org,
                pod=pod,
            )
            _link(bind, "agent_surfaces", "webhook_secret_id", row_id, secret)

    for row_id, access, app_secret, verify in _rows(
        bind,
        "SELECT id, access_token, app_secret, verify_token FROM surface_whatsapp_numbers",
    ):
        values = {
            key: text
            for key, text in (
                ("access_token", legacy.text(access)),
                ("app_secret", legacy.text(app_secret)),
                ("verify_token", legacy.text(verify)),
            )
            if text
        }
        if values:
            secret = vault.put(
                value=values,
                purpose="agent_surfaces.whatsapp_number.credentials",
                owner_table="surface_whatsapp_numbers",
                org=None,
            )
            _link(
                bind,
                "surface_whatsapp_numbers",
                "credentials_secret_id",
                row_id,
                secret,
            )

    for row_id, run_id, raw in _rows(
        bind,
        "SELECT id, run_id, payload -> 'encrypted_mcp' FROM agent_host_commands "
        "WHERE payload -> 'encrypted_mcp' IS NOT NULL",
    ):
        frame = legacy.json(raw)
        if frame is None or run_id is None:
            bind.execute(
                sa.text(
                    "UPDATE agent_host_commands SET payload = payload - 'encrypted_mcp' WHERE id = :id"
                ),
                {"id": row_id},
            )
            continue
        sealed = vault.seal_transient(
            frame, purpose="agent.agent_host.mcp", bindings=[str(run_id)]
        )
        bind.execute(
            sa.text(
                "UPDATE agent_host_commands SET payload = jsonb_set(payload, "
                "'{encrypted_mcp}', to_jsonb(CAST(:sealed AS text))) WHERE id = :id"
            ),
            {"sealed": sealed, "id": row_id},
        )

    logger.info("vault migration: moved %d secrets into the vault", vault.count)


def upgrade() -> None:
    for table, columns in _OWNERS:
        for column in columns:
            op.execute(
                f"ALTER TABLE {table} ADD COLUMN {column} uuid "
                f"REFERENCES vault_secrets(id) UNIQUE"
            )

    _move_secrets(op.get_bind())

    op.drop_column("accounts", "credentials")
    op.drop_column("auth_configs", "config")
    op.drop_column("agent_runtime_profiles", "credentials")
    op.drop_column("agent_surfaces", "webhook_secret")
    for column in ("access_token", "app_secret", "verify_token"):
        op.drop_column("surface_whatsapp_numbers", column)

    for table, columns in _OWNERS:
        cols = ", ".join(columns)
        args = ", ".join(f"'{column}'" for column in columns)
        op.execute(
            f"CREATE TRIGGER vault_release_{table} AFTER DELETE OR UPDATE OF {cols} "
            f"ON {table} FOR EACH ROW EXECUTE FUNCTION vault_release_owned_secrets({args})"
        )


def downgrade() -> None:
    bind = op.get_bind()
    held = bind.execute(sa.text("SELECT count(*) FROM vault_secrets")).scalar_one()
    if held:
        raise RuntimeError(
            f"Refusing to downgrade 0044_vault_cutover: the vault holds {held} secrets, and "
            "their pre-vault encrypted form cannot be rebuilt here. Restore the "
            "database backup taken before the upgrade instead."
        )
    for table, _columns in _OWNERS:
        op.execute(f"DROP TRIGGER IF EXISTS vault_release_{table} ON {table}")
    op.add_column(
        "accounts", sa.Column("credentials", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "auth_configs", sa.Column("config", postgresql.JSONB(), nullable=True)
    )
    op.add_column(
        "agent_runtime_profiles",
        sa.Column("credentials", postgresql.JSONB(), nullable=True),
    )
    op.add_column(
        "agent_surfaces", sa.Column("webhook_secret", sa.Text(), nullable=True)
    )
    for column in ("access_token", "app_secret", "verify_token"):
        op.add_column(
            "surface_whatsapp_numbers", sa.Column(column, sa.Text(), nullable=True)
        )
    for table, columns in _OWNERS:
        for column in columns:
            op.drop_column(table, column)
