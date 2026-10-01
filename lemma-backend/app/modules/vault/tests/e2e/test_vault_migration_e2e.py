"""0044 moves every stored secret into the vault, in every format it was written in.

Through the deployment path -- ``alembic upgrade`` in a subprocess, against a
fresh database -- because the migration is the only thing that will ever read
the old formats, and it gets one chance. Rows are planted at 0043 exactly as
the retired cipher wrote them: ``lemma-secret-v2`` dicts, ``fernet-json-v1``,
the ``lsenc1:`` string form, and plaintext from before encryption at rest.
"""

from __future__ import annotations

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
from app.modules.vault.contracts import SecretScope, open_json
from app.modules.vault.services.keyring import VaultKeyring
from app.modules.vault.services.store import SqlVault

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0043_vault_tables"
KEY = Fernet.generate_key()
KID = derive_kid(KEY)


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


def test_every_legacy_format_moves_into_the_vault_and_reads_back_identically() -> None:
    org, user, pod, run = uuid4(), uuid4(), uuid4(), uuid4()
    ids = {
        name: uuid4()
        for name in ("a1", "a2", "a3", "a4", "c1", "p1", "p2", "s1", "s2", "w1", "h1")
    }

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
            conn.execute("SET session_replication_role = replica")
            account = {
                "organization_id": str(org),
                "user_id": str(user),
                "status": "CONNECTED",
            }
            _insert(
                conn,
                "accounts",
                {
                    **account,
                    "id": ids["a1"],
                    "credentials": _v2(
                        {"access_token": "a1", "expires_at": 1893456000}
                    ),
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
            profile = {
                "organization_id": str(org),
                "scope": "ORGANIZATION",
                "status": "ACTIVE",
            }
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
                {
                    **surface,
                    "id": ids["s1"],
                    "name": "s1",
                    "webhook_secret": _lsenc1("w1"),
                },
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
                "agent_host_commands",
                {
                    "id": ids["h1"],
                    "run_id": str(run),
                    "payload": {"encrypted_mcp": _v2({"url": "mcp"}), "keep": 1},
                },
            )
            # The seed's parents are fillers, so the migration's own UPDATEs
            # would trip their foreign keys. This database is thrown away.
            for table in (
                "accounts",
                "auth_configs",
                "agent_runtime_profiles",
                "agent_surfaces",
                "surface_whatsapp_numbers",
                "agent_host_commands",
            ):
                conn.execute(f"ALTER TABLE {table} DISABLE TRIGGER ALL")
            conn.commit()

        assert (result := migrate("upgrade", "head")).returncode == 0, result.stderr

        with psycopg.connect(sync_url) as conn:
            gone = conn.execute(
                "SELECT table_name, column_name FROM information_schema.columns WHERE "
                "(table_name, column_name) IN (('accounts','credentials'), ('auth_configs','config'), "
                "('agent_runtime_profiles','credentials'), ('agent_surfaces','webhook_secret'), "
                "('surface_whatsapp_numbers','access_token'))"
            ).fetchall()
            assert gone == []
            refs = {
                name: conn.execute(
                    f"SELECT {column} FROM {table} WHERE id = %s", (ids[name],)
                ).fetchone()[0]
                for name, table, column in (
                    ("a1", "accounts", "credentials_secret_id"),
                    ("a2", "accounts", "credentials_secret_id"),
                    ("a3", "accounts", "credentials_secret_id"),
                    ("a4", "accounts", "credentials_secret_id"),
                    ("c1", "auth_configs", "config_secret_id"),
                    ("p1", "agent_runtime_profiles", "secrets_secret_id"),
                    ("p2", "agent_runtime_profiles", "secrets_secret_id"),
                    ("s1", "agent_surfaces", "webhook_secret_id"),
                    ("s2", "agent_surfaces", "webhook_secret_id"),
                    ("w1", "surface_whatsapp_numbers", "credentials_secret_id"),
                )
            }
            p1_config = conn.execute(
                "SELECT config FROM agent_runtime_profiles WHERE id = %s", (ids["p1"],)
            ).fetchone()[0]
            sealed_mcp, kept = conn.execute(
                "SELECT payload ->> 'encrypted_mcp', payload -> 'keep' FROM agent_host_commands WHERE id = %s",
                (ids["h1"],),
            ).fetchone()
            triggers = {
                row[0]
                for row in conn.execute(
                    "SELECT tgname FROM pg_trigger WHERE tgname LIKE 'vault_release_%%'"
                ).fetchall()
            }

        assert refs["a4"] is None and refs["p2"] is None
        assert p1_config == {"base_url": "https://llm"}
        assert kept == 1
        assert triggers == {
            "vault_release_accounts",
            "vault_release_auth_configs",
            "vault_release_agent_runtime_profiles",
            "vault_release_agent_surfaces",
            "vault_release_surface_whatsapp_numbers",
        }

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
        }
        assert _open_mcp(database_url, sealed_mcp, run) == {"url": "mcp"}

        refused = migrate("downgrade", BEFORE)
        assert refused.returncode != 0
        assert "Restore the database backup" in refused.stdout + refused.stderr


def _keyring(database_url: str) -> tuple[VaultKeyring, object]:
    root = LocalKeyringRoot(
        "static", lambda: Keyring(primary_kid=KID, keys={KID: KeyMaterial(KID, KEY)})
    )
    engine = create_async_engine(database_url)
    return VaultKeyring(root, async_sessionmaker(engine)), engine


def _reveal_all(
    database_url: str, refs: dict, *, org: UUID, user: UUID, pod: UUID
) -> dict:
    import asyncio

    scopes = {
        "a": (SecretScope(org, user_id=user), "connectors.account.credentials"),
        "c": (SecretScope(org), "connectors.auth_config.config"),
        "p": (SecretScope(org), "agent.runtime_profile.secrets"),
        "s": (SecretScope(org, pod_id=pod), "agent_surfaces.surface.webhook_secret"),
        "w": (SecretScope.system(), "agent_surfaces.whatsapp_number.credentials"),
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


def _open_mcp(database_url: str, sealed: str, run: UUID) -> dict:
    import asyncio

    async def run_open() -> dict:
        keyring, engine = _keyring(database_url)
        try:
            return await open_json(
                sealed,
                purpose="agent.agent_host.mcp",
                bindings=[str(run)],
                keyring=keyring,
            )
        finally:
            await engine.dispose()

    return asyncio.run(run_open())
