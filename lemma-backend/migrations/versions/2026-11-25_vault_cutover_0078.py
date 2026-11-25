"""Copy every stored secret into the vault, next to where it is now.

Secrets are encrypted column by column with Fernet, with no associated data, so
a ciphertext copied from one tenant's row into another's decrypts for the other.
Each now gets a copy in ``vault_secrets`` -- its own data key (AES-256-GCM)
wrapped by the active key-encryption key, bound to its owner's scope and to its
purpose -- and the owner row a ``*_secret_id`` pointing at it.

This is the expand half. The old columns stay, so the release before this one
keeps running against this schema during a rolling deploy:

- every old column is kept and still readable; those that were ``NOT NULL``
  become nullable, because the new code writes the vault, not them;
- a write to an old column by a process still on the previous release (a
  refreshed token, a new row) deletes the vault copy and clears the reference
  (``vault_legacy_write_wins``), so the newer value is the one read, from the
  old column, until it is next written -- never a stale copy;
- the old columns are dropped, with the Fernet cipher, by the next release's
  contract migration, after it copies anything still only there.

Owners, the column(s) copied, and the scope each copy is bound to:

=============================  ==============================  ==================
owner table                    old column(s)                   scope
=============================  ==============================  ==================
accounts                       credentials                     org, user
auth_configs                   config                          org
agent_runtime_profiles         credentials, config.headers     org
agent_surfaces                 webhook_secret                  org, pod
surface_whatsapp_numbers       access_token, app_secret,       deployment
                               verify_token
slack_configuration_tokens     access_token, refresh_token     org
agent_surface_web_widgets      signing_secret                  pod
mcp_event_subscriptions        secret_ciphertext               pod, user
connector_event_subscriptions  secret_ciphertext               org, user
machine_login_backups          wrapped_key                     sandbox owner
machine_services               env_ciphertext                  sandbox owner
=============================  ==============================  ==================

Requirements: the process running this needs the backend's key configuration --
``SECRET_ENCRYPTION_KEY(SET)`` to read the old rows, and the root
(``GCP_KMS_KEY_NAME`` with ``roles/cloudkms.cryptoKeyEncrypterDecrypter``, or the
keyset) to unwrap the active KEK. An installation with no secrets never calls
the root. Any failure rolls the migration back; fix and re-run.

Downgrade drops the references and the copies, and refuses while any secret is
held only in the vault (written by this release, with its old column cleared).

Revision ID: 0078_vault_cutover
Revises: 0077_vault_tables
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
from typing import NamedTuple

import sqlalchemy as sa
from alembic import op
from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

revision = "0078_vault_cutover"
down_revision = "0077_vault_tables"
branch_labels = None
depends_on = None

logger = logging.getLogger("alembic.runtime.migration")


class _Owner(NamedTuple):
    table: str
    secret_column: str
    legacy_columns: tuple[str, ...]
    #: Old columns that were NOT NULL and become nullable.
    relax: tuple[str, ...] = ()


_OWNERS: tuple[_Owner, ...] = (
    _Owner("accounts", "credentials_secret_id", ("credentials",)),
    _Owner("auth_configs", "config_secret_id", ("config",)),
    _Owner("agent_runtime_profiles", "secrets_secret_id", ("credentials",)),
    _Owner("agent_surfaces", "webhook_secret_id", ("webhook_secret",)),
    _Owner(
        "surface_whatsapp_numbers",
        "credentials_secret_id",
        ("access_token", "app_secret", "verify_token"),
    ),
    _Owner(
        "slack_configuration_tokens",
        "tokens_secret_id",
        ("access_token", "refresh_token"),
        relax=("access_token", "refresh_token"),
    ),
    _Owner(
        "agent_surface_web_widgets",
        "signing_secret_id",
        ("signing_secret",),
        relax=("signing_secret",),
    ),
    _Owner(
        "mcp_event_subscriptions",
        "signing_secret_id",
        ("secret_ciphertext",),
        relax=("secret_ciphertext",),
    ),
    _Owner(
        "connector_event_subscriptions",
        "signing_secret_id",
        ("secret_ciphertext",),
        relax=("secret_ciphertext",),
    ),
    _Owner(
        "machine_login_backups",
        "key_secret_id",
        ("wrapped_key",),
        relax=("wrapped_key",),
    ),
    _Owner("machine_services", "env_secret_id", ("env_ciphertext",)),
)

#: A write to an old column by the previous release wins over the vault copy:
#: this clears the reference before the row is written, and the release
#: trigger installed on the old columns too deletes the copy (with its audit
#: event) after. The new code reads the old column until it next writes the
#: value, and never writes a non-null old value itself, so never trips this.
_LEGACY_WRITE_WINS = """
CREATE OR REPLACE FUNCTION vault_legacy_write_wins() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  secret_col text := TG_ARGV[0];
  col text;
  old_row jsonb := to_jsonb(OLD);
  new_row jsonb := to_jsonb(NEW);
BEGIN
  IF (new_row ->> secret_col) IS DISTINCT FROM (old_row ->> secret_col) THEN
    RETURN NEW;
  END IF;
  IF (old_row ->> secret_col) IS NULL THEN
    RETURN NEW;
  END IF;
  FOREACH col IN ARRAY TG_ARGV[1:] LOOP
    IF (new_row -> col) IS DISTINCT FROM (old_row -> col)
       AND (new_row -> col) IS NOT NULL AND (new_row -> col) <> 'null'::jsonb THEN
      NEW := jsonb_populate_record(NEW, jsonb_build_object(secret_col, NULL));
      RETURN NEW;
    END IF;
  END LOOP;
  RETURN NEW;
END $$;
"""


# --------------------------------------------------------------------------
# Frozen legacy decoding (the app/core/crypto cipher's read side, as written)
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
                retry_seconds=crypto_settings.gcp_kms_retry_seconds,
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
        """The active KEK: the one processes created at start, or a first one."""
        if self._kek is None:
            from app.core.crypto.roots.factory import (
                get_root_key_provider,
                validate_root_key_config,
            )

            validate_root_key_config()
            root = get_root_key_provider()
            existing = self._bind.execute(
                sa.text(
                    "SELECT id, root_key_ref, wrapped_key FROM vault_keys "
                    "WHERE purpose = 'encrypt' AND state = 'active'"
                )
            ).first()
            if existing is not None:
                kek_id, ref, blob = existing
                aad = _aad("lemma-vault/kek/v1", str(kek_id), "encrypt")
                self._kek = (kek_id, _run(root.unwrap(ref, bytes(blob), aad=aad)))
                return self._kek
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
# Copying
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


#: A sandbox is owned by a person or by a pod; its secrets are bound to that owner.
_SANDBOX_SCOPE = (
    "CASE WHEN s.owner_kind = 'pod' THEN s.owner_id END AS pod_id, "
    "CASE WHEN s.owner_kind = 'user' THEN s.owner_id END AS user_id"
)


def _copy_secrets(bind: sa.Connection) -> int:
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
        values = _texts(
            legacy,
            access_token=access,
            app_secret=app_secret,
            verify_token=verify,
        )
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

    for row_id, org, access, refresh in _rows(
        bind,
        "SELECT id, organization_id, access_token, refresh_token "
        "FROM slack_configuration_tokens",
    ):
        values = _texts(legacy, access_token=access, refresh_token=refresh)
        if values:
            secret = vault.put(
                value=values,
                purpose="agent_surfaces.slack_configuration.tokens",
                owner_table="slack_configuration_tokens",
                org=org,
            )
            _link(
                bind, "slack_configuration_tokens", "tokens_secret_id", row_id, secret
            )

    for row_id, pod, raw in _rows(
        bind, "SELECT id, pod_id, signing_secret FROM agent_surface_web_widgets"
    ):
        value = legacy.text(raw)
        if value:
            secret = vault.put(
                value=value,
                purpose="agent_surfaces.web_widget.signing_secret",
                owner_table="agent_surface_web_widgets",
                org=None,
                pod=pod,
            )
            _link(
                bind, "agent_surface_web_widgets", "signing_secret_id", row_id, secret
            )

    for row_id, pod, user, raw in _rows(
        bind,
        "SELECT id, pod_id, user_id, secret_ciphertext FROM mcp_event_subscriptions",
    ):
        value = legacy.text(raw)
        if value:
            secret = vault.put(
                value=value,
                purpose="mcp_access.event_subscription.secret",
                owner_table="mcp_event_subscriptions",
                org=None,
                pod=pod,
                user=user,
            )
            _link(bind, "mcp_event_subscriptions", "signing_secret_id", row_id, secret)

    for row_id, org, user, raw in _rows(
        bind,
        "SELECT id, organization_id, user_id, secret_ciphertext "
        "FROM connector_event_subscriptions",
    ):
        value = legacy.text(raw)
        if value:
            secret = vault.put(
                value=value,
                purpose="connectors.event_subscription.secret",
                owner_table="connector_event_subscriptions",
                org=org,
                user=user,
            )
            _link(
                bind,
                "connector_event_subscriptions",
                "signing_secret_id",
                row_id,
                secret,
            )

    for row_id, pod, user, raw in _rows(
        bind,
        f"SELECT b.id, {_SANDBOX_SCOPE}, b.wrapped_key FROM machine_login_backups b "
        "JOIN sandboxes s ON s.id = b.sandbox_id",
    ):
        value = legacy.text(raw)
        if value:
            secret = vault.put(
                value=value,
                purpose="workspace.login_backup.key",
                owner_table="machine_login_backups",
                org=None,
                pod=pod,
                user=user,
            )
            _link(bind, "machine_login_backups", "key_secret_id", row_id, secret)

    for row_id, pod, user, raw in _rows(
        bind,
        f"SELECT m.id, {_SANDBOX_SCOPE}, m.env_ciphertext FROM machine_services m "
        "JOIN sandboxes s ON s.id = m.sandbox_id WHERE m.env_ciphertext IS NOT NULL",
    ):
        value = legacy.text(raw)
        env = json.loads(value) if value else None
        if env:
            secret = vault.put(
                value=env,
                purpose="workspace.machine_service.env",
                owner_table="machine_services",
                org=None,
                pod=pod,
                user=user,
            )
            _link(bind, "machine_services", "env_secret_id", row_id, secret)

    return vault.count


def _texts(legacy: _Legacy, **raw: str | None) -> dict[str, str]:
    decoded = {name: legacy.text(value) for name, value in raw.items()}
    return {name: value for name, value in decoded.items() if value}


# --------------------------------------------------------------------------
# Upgrade / downgrade
# --------------------------------------------------------------------------
def upgrade() -> None:
    op.execute(_LEGACY_WRITE_WINS)
    for owner in _OWNERS:
        op.execute(
            f"ALTER TABLE {owner.table} ADD COLUMN {owner.secret_column} uuid "
            f"REFERENCES vault_secrets(id) UNIQUE"
        )
        for column in owner.relax:
            op.alter_column(owner.table, column, nullable=True)

    moved = _copy_secrets(op.get_bind())
    logger.info("vault migration: copied %d secrets into the vault", moved)

    for owner in _OWNERS:
        legacy_cols = ", ".join(owner.legacy_columns)
        op.execute(
            f"CREATE TRIGGER vault_release_{owner.table} "
            f"AFTER DELETE OR UPDATE OF {owner.secret_column}, {legacy_cols} "
            f"ON {owner.table} "
            f"FOR EACH ROW EXECUTE FUNCTION "
            f"vault_release_owned_secrets('{owner.secret_column}')"
        )
        legacy_args = ", ".join(f"'{c}'" for c in owner.legacy_columns)
        op.execute(
            f"CREATE TRIGGER vault_legacy_write_{owner.table} "
            f"BEFORE UPDATE OF {legacy_cols} ON {owner.table} "
            f"FOR EACH ROW EXECUTE FUNCTION "
            f"vault_legacy_write_wins('{owner.secret_column}', {legacy_args})"
        )


def downgrade() -> None:
    bind = op.get_bind()
    only_in_vault = []
    for owner in _OWNERS:
        cleared = " AND ".join(f"{c} IS NULL" for c in owner.legacy_columns)
        count = bind.execute(
            sa.text(
                f"SELECT count(*) FROM {owner.table} "
                f"WHERE {owner.secret_column} IS NOT NULL AND {cleared}"
            )
        ).scalar_one()
        if count:
            only_in_vault.append(f"{owner.table}: {count}")
    if only_in_vault:
        raise RuntimeError(
            "Refusing to downgrade 0078_vault_cutover: these secrets exist only in "
            "the vault and the previous release cannot read them -- "
            + ", ".join(only_in_vault)
            + ". Restore the backup taken before the upgrade instead."
        )
    for owner in _OWNERS:
        op.execute(
            f"DROP TRIGGER IF EXISTS vault_legacy_write_{owner.table} ON {owner.table}"
        )
        op.execute(
            f"DROP TRIGGER IF EXISTS vault_release_{owner.table} ON {owner.table}"
        )
        op.drop_column(owner.table, owner.secret_column)
    owners = ", ".join(f"'{owner.table}'" for owner in _OWNERS)
    op.execute(f"DELETE FROM vault_secrets WHERE owner_table IN ({owners})")
    op.execute("DROP FUNCTION IF EXISTS vault_legacy_write_wins()")
    # The NOT NULL constraints relaxed on upgrade are not put back: rows written
    # while upgraded may hold a value only in the vault, now gone with it.
