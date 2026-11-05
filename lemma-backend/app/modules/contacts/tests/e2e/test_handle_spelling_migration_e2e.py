"""Migration 0057 spells every handle one way, and keeps opt-outs by hash.

A number WhatsApp named by its digits becomes E.164; a host identity names the
product that vouched for it (``default`` for every widget so far); a handle
marked unsubscribed on its row becomes a ``contact_suppressions`` row, and the
column goes. The downgrade spells them back.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0056_agent_surface_bundle_work"
AFTER = "0057_contacts_for_everyone"

_FILLER = {
    "uuid": "gen_random_uuid()",
    "timestamp with time zone": "now()",
    "timestamp without time zone": "now()",
    "boolean": "false",
    "integer": "0",
    "bigint": "0",
    "jsonb": "'{}'::jsonb",
    "json": "'{}'::json",
}


def _insert_row(connection: psycopg.Connection, table: str, values: dict) -> None:
    """A row with ``values`` and a filler for every other required column."""
    required = connection.execute(
        "SELECT column_name, data_type FROM information_schema.columns "
        "WHERE table_name = %s AND is_nullable = 'NO' AND column_default IS NULL",
        [table],
    ).fetchall()
    names = list(values)
    sql_values = ["%s"] * len(values)
    for name, data_type in required:
        if name in values:
            continue
        names.append(name)
        sql_values.append(_FILLER.get(data_type, "'x'"))
    connection.execute(
        f"INSERT INTO {table} ({', '.join(names)}) VALUES ({', '.join(sql_values)})",
        list(values.values()),
    )


def test_handles_are_respelled_and_opt_outs_kept_by_hash() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        environment = {**os.environ, "DATABASE_URL": database_url}
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")

        def migrate(direction: str, revision: str) -> None:
            result = subprocess.run(
                ["uv", "run", "--no-sync", "alembic", direction, revision],
                cwd=BACKEND,
                env=environment,
                capture_output=True,
                text=True,
                timeout=180,
            )
            assert result.returncode == 0, result.stdout + result.stderr

        def handles() -> dict[str, tuple[str, str | None]]:
            with psycopg.connect(plain_url) as connection:
                rows = connection.execute(
                    "SELECT kind, value, issuer FROM contact_identities"
                ).fetchall()
            return {kind: (value, issuer) for kind, value, issuer in rows}

        migrate("upgrade", BEFORE)
        pod_id, contact_id = uuid4(), uuid4()
        with psycopg.connect(plain_url) as connection:
            # Only the rows the respelling reads; nothing else of a pod.
            connection.execute("SET session_replication_role = replica")
            _insert_row(connection, "pods", {"id": pod_id})
            _insert_row(connection, "contacts", {"id": contact_id, "pod_id": pod_id})
            for kind, value, unsubscribed in (
                ("PHONE", "447700900123", True),
                ("HOST", "host:cust-42", False),
                ("EMAIL", "dana@client.example", False),
            ):
                _insert_row(
                    connection,
                    "contact_identities",
                    {
                        "id": uuid4(),
                        "contact_id": contact_id,
                        "pod_id": pod_id,
                        "kind": kind,
                        "value": value,
                        "strength": "CHANNEL",
                        "unsubscribed_at": "2026-10-01T00:00:00Z"
                        if unsubscribed
                        else None,
                    },
                )

        migrate("upgrade", AFTER)

        assert handles() == {
            "PHONE": ("+447700900123", None),
            "HOST": ("host:default:cust-42", "default"),
            "EMAIL": ("dana@client.example", None),
        }
        with psycopg.connect(plain_url) as connection:
            suppressed = connection.execute(
                "SELECT pod_id, kind, value_hash, reason FROM contact_suppressions"
            ).fetchall()
            gone = connection.execute(
                "SELECT 1 FROM information_schema.columns WHERE table_name = "
                "'contact_identities' AND column_name = 'unsubscribed_at'"
            ).fetchone()
            raw = connection.execute(
                "SELECT value_raw FROM contact_identities WHERE kind = 'PHONE'"
            ).fetchone()
            issuer_default = connection.execute(
                "SELECT column_default FROM information_schema.columns WHERE "
                "table_name = 'agent_surface_web_widgets' "
                "AND column_name = 'host_issuer'"
            ).fetchone()
        # The hash the application keys by: sha256 of the normalised handle.
        assert suppressed == [
            (
                pod_id,
                "PHONE",
                hashlib.sha256(b"+447700900123").digest(),
                "unsubscribe",
            )
        ]
        assert gone is None
        assert raw == ("447700900123",)
        assert issuer_default is not None and "default" in issuer_default[0]

        migrate("downgrade", BEFORE)

        with psycopg.connect(plain_url) as connection:
            rows = connection.execute(
                "SELECT kind, value, unsubscribed_at IS NOT NULL "
                "FROM contact_identities"
            ).fetchall()
        assert {kind: (value, stopped) for kind, value, stopped in rows} == {
            "PHONE": ("447700900123", True),
            "HOST": ("host:cust-42", False),
            "EMAIL": ("dana@client.example", False),
        }

        migrate("upgrade", "head")
