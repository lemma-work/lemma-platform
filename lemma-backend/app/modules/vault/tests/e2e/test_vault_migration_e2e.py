"""0078 copies every stored secret into the vault, in every format it was written in.

Through the deployment path -- ``alembic upgrade`` in a subprocess, against a
fresh database -- because the migration reads formats nothing else will write
again. Rows are planted at 0077 exactly as the Fernet cipher wrote them:
``lemma-secret-v2`` dicts, ``fernet-json-v1``, the ``lsenc1:`` string form, and
plaintext from before encryption at rest.

It is the expand half, so the test also holds it to that: the old columns are
still there and unchanged, a write to one by the previous release wins over
the copy, and downgrade refuses only while a secret exists in the vault alone.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.core.crypto.keys import derive_kid
from app.core.crypto.ports import KeyMaterial, Keyring
from app.core.crypto.roots.local import LocalKeyringRoot
from app.core.test_utils import get_postgres_container, get_postgres_url
from app.modules.vault.contracts import SecretScope
from app.modules.vault.services.keyring import VaultKeyring
from app.modules.vault.services.store import SqlVault

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0077_vault_tables"
KEY = Fernet.generate_key()
KID = derive_kid(KEY)

#: name -> (table, reference column)
_REFS = {
    "a1": ("accounts", "credentials_secret_id"),
    "a2": ("accounts", "credentials_secret_id"),
    "a3": ("accounts", "credentials_secret_id"),
    "a4": ("accounts", "credentials_secret_id"),
    "c1": ("auth_configs", "config_secret_id"),
    "p1": ("agent_runtime_profiles", "secrets_secret_id"),
    "p2": ("agent_runtime_profiles", "secrets_secret_id"),
    "s1": ("agent_surfaces", "webhook_secret_id"),
    "s2": ("agent_surfaces", "webhook_secret_id"),
    "w1": ("surface_whatsapp_numbers", "credentials_secret_id"),
    "k1": ("slack_configuration_tokens", "tokens_secret_id"),
    "g1": ("agent_surface_web_widgets", "signing_secret_id"),
    "m1": ("mcp_event_subscriptions", "signing_secret_id"),
    "e1": ("connector_event_subscriptions", "signing_secret_id"),
    "b1": ("machine_login_backups", "key_secret_id"),
    "v1": ("machine_services", "env_secret_id"),
}


def _v2(value: dict | str) -> dict:
    payload = value if isinstance(value, str) else json.dumps(value, sort_keys=True)
    token = Fernet(KEY).encrypt(payload.encode())
    return {
        "_encrypted": "lemma-secret-v2",
        "kid": KID,
        "alg": "fernet",
        "ct": base64.urlsafe_b64encode(token).decode(),
    }


def _v1(value: dict) -> dict:
    token = Fernet(KEY).encrypt(json.dumps(value).encode()).decode()
    return {"_encrypted": "fernet-json-v1", "ciphertext": token}


def _lsenc1(value: str) -> str:
    envelope = json.dumps(_v2(value), separators=(",", ":")).encode()
    return "lsenc1:" + base64.urlsafe_b64encode(envelope).decode()


_FILLERS = {
    "uuid": lambda: str(uuid4()),
    "text": lambda: "x",
    "character varying": lambda: "x",
    "boolean": lambda: False,
    "integer": lambda: 0,
    "bigint": lambda: 0,
    "jsonb": lambda: "{}",
    "timestamp with time zone": lambda: "2026-01-01T00:00:00Z",
    "ARRAY": lambda: "{}",
}


def _insert(conn: psycopg.Connection, table: str, values: dict) -> None:
    """Insert ``values`` plus a filler for every other required column.

    Foreign keys are off for the seed (``session_replication_role``): what is
    under test is the secret columns, not a full tenant graph behind them.
    """
    required = conn.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = %s AND is_nullable = 'NO' AND column_default IS NULL",
        (table,),
    ).fetchall()
    row = {name: _FILLERS[kind]() for name, kind in required if name not in values}
    row.update(
        {
            k: json.dumps(v) if isinstance(v, (dict, list)) else v
            for k, v in values.items()
        }
    )
    columns = ", ".join(row)
    params = ", ".join(["%s"] * len(row))
    conn.execute(
        f"INSERT INTO {table} ({columns}) VALUES ({params})", list(row.values())
    )


def _seed(conn: psycopg.Connection, ids: dict, *, org, user, pod) -> None:
    conn.execute("SET session_replication_role = replica")
    account = {"organization_id": str(org), "user_id": str(user), "status": "CONNECTED"}
    _insert(
        conn,
        "accounts",
        {
            **account,
            "id": ids["a1"],
            "credentials": _v2({"access_token": "a1", "expires_at": 1893456000}),
        },
    )
    _insert(
        conn,
        "accounts",
        {**account, "id": ids["a2"], "credentials": _v1({"api_key": "a2"})},
    )
    _insert(
        conn,
        "accounts",
        {**account, "id": ids["a3"], "credentials": {"bot_token": "a3"}},
    )
    _insert(conn, "accounts", {**account, "id": ids["a4"]})
    _insert(
        conn,
        "auth_configs",
        {
            "id": ids["c1"],
            "organization_id": str(org),
            "config": _v2({"client_secret": "c1", "client_id": "public"}),
        },
    )
    profile = {"organization_id": str(org), "scope": "ORGANIZATION", "status": "ACTIVE"}
    _insert(
        conn,
        "agent_runtime_profiles",
        {
            **profile,
            "id": ids["p1"],
            "name": "p1",
            "credentials": _v2({"api_key": "p1"}),
            "config": {"headers": {"X-Key": "h1"}, "base_url": "https://llm"},
        },
    )
    _insert(
        conn,
        "agent_runtime_profiles",
        {
            **profile,
            "id": ids["p2"],
            "name": "p2",
            "config": {"base_url": "https://llm"},
        },
    )
    surface = {"organization_id": str(org), "pod_id": str(pod)}
    _insert(
        conn,
        "agent_surfaces",
        {**surface, "id": ids["s1"], "name": "s1", "webhook_secret": _lsenc1("w1")},
    )
    _insert(
        conn,
        "agent_surfaces",
        {**surface, "id": ids["s2"], "name": "s2", "webhook_secret": "w2"},
    )
    _insert(
        conn,
        "surface_whatsapp_numbers",
        {
            "id": ids["w1"],
            "status": "AVAILABLE",
            "access_token": _lsenc1("t1"),
            "verify_token": "v1",
        },
    )
    _insert(
        conn,
        "slack_configuration_tokens",
        {
            "id": ids["k1"],
            "organization_id": str(org),
            "access_token": _lsenc1("xoxe.a"),
            "refresh_token": _lsenc1("xoxe-r"),
        },
    )
    _insert(
        conn,
        "agent_surface_web_widgets",
        {"id": ids["g1"], "pod_id": str(pod), "signing_secret": _lsenc1("g1")},
    )
    _insert(
        conn,
        "mcp_event_subscriptions",
        {
            "id": ids["m1"],
            "pod_id": str(pod),
            "user_id": str(user),
            "secret_ciphertext": _lsenc1("m1"),
        },
    )
    _insert(
        conn,
        "connector_event_subscriptions",
        {
            "id": ids["e1"],
            "organization_id": str(org),
            "user_id": str(user),
            "secret_ciphertext": _lsenc1("e1"),
        },
    )
    sandbox = uuid4()
    _insert(
        conn,
        "sandboxes",
        {
            "id": str(sandbox),
            "kind": "workspace",
            "owner_kind": "user",
            "owner_id": str(user),
            "visibility": "private",
            "billing_scope": "user",
        },
    )
    _insert(
        conn,
        "machine_login_backups",
        {"id": ids["b1"], "sandbox_id": str(sandbox), "wrapped_key": _lsenc1("a2V5")},
    )
    _insert(
        conn,
        "machine_services",
        {
            "id": ids["v1"],
            "sandbox_id": str(sandbox),
            "env_ciphertext": _lsenc1(json.dumps({"API_TOKEN": "v1"})),
        },
    )
    # The seed's parents are fillers, so the migration's own UPDATEs would
    # trip their foreign keys. This database is thrown away.
    for table in {table for table, _ in _REFS.values()} | {"sandboxes"}:
        conn.execute(f"ALTER TABLE {table} DISABLE TRIGGER ALL")
    conn.commit()


def test_every_legacy_format_is_copied_into_the_vault_and_reads_back_identically() -> (
    None
):
    org, user, pod = uuid4(), uuid4(), uuid4()
    ids = {name: uuid4() for name in _REFS}

    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")
        env = {
            **os.environ,
            "DATABASE_URL": database_url,
            "SECRET_ENCRYPTION_KEY": KEY.decode(),
            "SECRET_KEY_PROVIDER": "static",
            "ENVIRONMENT": "testing",
        }

        def migrate(direction: str, revision: str) -> subprocess.CompletedProcess:
            return subprocess.run(
                ["uv", "run", "--no-sync", "alembic", direction, revision],
                cwd=BACKEND,
                env=env,
                capture_output=True,
                text=True,
                timeout=180,
            )

        assert (result := migrate("upgrade", BEFORE)).returncode == 0, result.stderr
        with psycopg.connect(sync_url) as conn:
            _seed(conn, ids, org=org, user=user, pod=pod)
            legacy_before = conn.execute(
                "SELECT credentials FROM accounts WHERE id = %s", (ids["a1"],)
            ).fetchone()[0]

        assert (result := migrate("upgrade", "head")).returncode == 0, result.stderr

        with psycopg.connect(sync_url) as conn:
            refs = {
                name: conn.execute(
                    f"SELECT {column} FROM {table} WHERE id = %s", (ids[name],)
                ).fetchone()[0]
                for name, (table, column) in _REFS.items()
            }
            # Expand only: the old value is still where the previous release reads it.
            legacy_after, p1_config = conn.execute(
                "SELECT a.credentials, p.config FROM accounts a, agent_runtime_profiles p "
                "WHERE a.id = %s AND p.id = %s",
                (ids["a1"], ids["p1"]),
            ).fetchone()
            triggers = {
                row[0]
                for row in conn.execute(
                    "SELECT tgname FROM pg_trigger WHERE tgname LIKE 'vault_%%'"
                ).fetchall()
            }

        assert refs["a4"] is None and refs["p2"] is None
        assert legacy_after == legacy_before
        assert p1_config == {"headers": {"X-Key": "h1"}, "base_url": "https://llm"}
        owners = {table for table, _ in _REFS.values()}
        assert {f"vault_release_{t}" for t in owners} <= triggers
        assert {f"vault_legacy_write_{t}" for t in owners} <= triggers

        revealed = _reveal_all(database_url, refs, org=org, user=user, pod=pod)
        assert revealed == {
            "a1": {"access_token": "a1", "expires_at": 1893456000},
            "a2": {"api_key": "a2"},
            "a3": {"bot_token": "a3"},
            "c1": {"client_secret": "c1", "client_id": "public"},
            "p1": {"credentials": {"api_key": "p1"}, "headers": {"X-Key": "h1"}},
            "s1": "w1",
            "s2": "w2",
            "w1": {"access_token": "t1", "verify_token": "v1"},
            "k1": {"access_token": "xoxe.a", "refresh_token": "xoxe-r"},
            "g1": "g1",
            "m1": "m1",
            "e1": "e1",
            "b1": "a2V5",
            "v1": {"API_TOKEN": "v1"},
        }

        with psycopg.connect(sync_url) as conn:
            # The previous release refreshes a token mid-rollout: its write wins,
            # and the copy it made stale is gone.
            conn.execute(
                "UPDATE accounts SET credentials = %s WHERE id = %s",
                (json.dumps(_v2({"access_token": "a1-refreshed"})), ids["a1"]),
            )
            # The new release writes a secret to the vault and clears the old column.
            conn.execute(
                "UPDATE agent_surfaces SET webhook_secret = NULL WHERE id = %s",
                (ids["s1"],),
            )
            conn.commit()
            a1_ref = conn.execute(
                "SELECT credentials_secret_id FROM accounts WHERE id = %s", (ids["a1"],)
            ).fetchone()[0]
            a1_copy = conn.execute(
                "SELECT count(*) FROM vault_secrets WHERE id = %s", (refs["a1"],)
            ).fetchone()[0]
            s1_ref = conn.execute(
                "SELECT webhook_secret_id FROM agent_surfaces WHERE id = %s",
                (ids["s1"],),
            ).fetchone()[0]
        assert a1_ref is None and a1_copy == 0
        assert s1_ref == refs["s1"]

        refused = migrate("downgrade", BEFORE)
        assert refused.returncode != 0
        assert "agent_surfaces: 1" in refused.stdout + refused.stderr

        with psycopg.connect(sync_url) as conn:
            conn.execute(
                "UPDATE agent_surfaces SET webhook_secret = %s WHERE id = %s",
                (_lsenc1("w1"), ids["s1"]),
            )
            conn.commit()
        assert (result := migrate("downgrade", BEFORE)).returncode == 0, result.stderr
        with psycopg.connect(sync_url) as conn:
            left = conn.execute(
                "SELECT count(*) FROM information_schema.columns "
                "WHERE (table_name, column_name) IN ("
                + ", ".join(f"('{t}', '{c}')" for t, c in set(_REFS.values()))
                + ")"
            ).fetchone()[0]
            copies = conn.execute("SELECT count(*) FROM vault_secrets").fetchone()[0]
        assert left == 0 and copies == 0


def _keyring(database_url: str) -> tuple[VaultKeyring, object]:
    root = LocalKeyringRoot(
        "static", lambda: Keyring(primary_kid=KID, keys={KID: KeyMaterial(KID, KEY)})
    )
    engine = create_async_engine(database_url)
    return VaultKeyring(root, async_sessionmaker(engine)), engine


def _reveal_all(
    database_url: str, refs: dict, *, org: UUID, user: UUID, pod: UUID
) -> dict:

    scopes = {
        "a": (SecretScope(org, user_id=user), "connectors.account.credentials"),
        "c": (SecretScope(org), "connectors.auth_config.config"),
        "p": (SecretScope(org), "agent.runtime_profile.secrets"),
        "s": (SecretScope(org, pod_id=pod), "agent_surfaces.surface.webhook_secret"),
        "w": (SecretScope.system(), "agent_surfaces.whatsapp_number.credentials"),
        "k": (SecretScope(org), "agent_surfaces.slack_configuration.tokens"),
        "g": (
            SecretScope(None, pod_id=pod),
            "agent_surfaces.web_widget.signing_secret",
        ),
        "m": (
            SecretScope(None, pod_id=pod, user_id=user),
            "mcp_access.event_subscription.secret",
        ),
        "e": (SecretScope(org, user_id=user), "connectors.event_subscription.secret"),
        "b": (SecretScope(None, user_id=user), "workspace.login_backup.key"),
        "v": (SecretScope(None, user_id=user), "workspace.machine_service.env"),
    }

    async def run() -> dict:
        keyring, engine = _keyring(database_url)
        try:
            async with async_sessionmaker(engine)() as session:
                vault = SqlVault(session, keyring)
                out = {}
                for name, secret_id in refs.items():
                    if secret_id is None:
                        continue
                    scope, purpose = scopes[name[0]]
                    out[name] = (
                        await vault.reveal(secret_id, expect=scope, purpose=purpose)
                    ).value()
                return out
        finally:
            await engine.dispose()

    return asyncio.run(run())
