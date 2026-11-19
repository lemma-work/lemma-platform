"""Migration 0073 turns every existing workspace into a default machine.

A workspace row already there when it runs takes the defaults: private,
billed to its user, the legacy 1x2 size it was built at -- which is how the
later move to 2x4 finds it -- and the workspace template. The four machine
tables exist after it and not after the downgrade, and upgrading again works.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0073_conversation_per_platform"
REVISION = "0074_machines"
TABLES = (
    "machine_connections",
    "machine_previews",
    "machine_sessions",
    "machine_login_backups",
    "machine_services",
)


def _migrate(database_url: str, direction: str, revision: str) -> None:
    result = subprocess.run(
        ["uv", "run", "--no-sync", "alembic", direction, revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]


def _a_workspace(plain_url: str):
    user_id = uuid4()
    with psycopg.connect(plain_url) as connection:
        connection.execute(
            "INSERT INTO sandboxes (id, kind, owner_kind, owner_id, slug, "
            "display_name, profile_name, profile_digest, created_at, updated_at) "
            "VALUES (%s, 'workspace', 'user', %s, 'default', 'Workspace', '', '', "
            "now(), now())",
            (user_id, user_id),
        )
    return user_id


def _machine_columns(plain_url: str, sandbox_id) -> tuple[object, ...]:
    with psycopg.connect(plain_url) as connection:
        return connection.execute(
            "SELECT template, size, fabric, capabilities, visibility, "
            "billing_scope, connection_id, run_slots, created_by "
            "FROM sandboxes WHERE id = %s",
            (sandbox_id,),
        ).fetchone()


def _tables(plain_url: str) -> set[str]:
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name = ANY(%s)",
            (list(TABLES),),
        ).fetchall()
    return {row[0] for row in rows}


def _has_agent_machine(plain_url: str) -> bool:
    with psycopg.connect(plain_url) as connection:
        row = connection.execute(
            "SELECT 1 FROM information_schema.columns "
            "WHERE table_name = 'agents' AND column_name = 'machine'"
        ).fetchone()
    return row is not None


def test_an_existing_workspace_becomes_a_default_machine_and_it_rolls_back() -> None:
    default_machine = ("workspace", "1x2", None, {}, "private", "user", None, 1, None)
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        workspace = _a_workspace(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        assert _machine_columns(plain_url, workspace) == default_machine
        assert _tables(plain_url) == set(TABLES)
        assert _has_agent_machine(plain_url)

        _migrate(database_url, "downgrade", BEFORE)
        assert _tables(plain_url) == set()
        assert not _has_agent_machine(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        assert _machine_columns(plain_url, workspace) == default_machine
        assert _tables(plain_url) == set(TABLES)


def _pairing_schema(plain_url: str) -> tuple[bool, set[str], set[str]]:
    """Whether the pairings table exists; the connection columns that exist;
    and those of ``secret_hash``/``home`` that take NULL."""
    with psycopg.connect(plain_url) as connection:
        table = connection.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_name = 'machine_connection_pairings'"
        ).fetchone()
        rows = connection.execute(
            "SELECT column_name, is_nullable FROM information_schema.columns "
            "WHERE table_name = 'machine_connections'"
        ).fetchall()
    columns = {name for name, _ in rows}
    nullable = {name for name, is_nullable in rows if is_nullable == "YES"} & {
        "secret_hash",
        "home",
    }
    return table is not None, columns & {"installation_id", "platform"}, nullable


def test_pairing_relaxes_the_connection_and_adds_its_codes_and_rolls_back() -> None:
    after = "0075_machine_connection_pairing"
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", REVISION)
        assert _pairing_schema(plain_url) == (False, set(), set())

        _migrate(database_url, "upgrade", after)
        paired = (True, {"installation_id", "platform"}, {"secret_hash", "home"})
        assert _pairing_schema(plain_url) == paired

        _migrate(database_url, "downgrade", REVISION)
        assert _pairing_schema(plain_url) == (False, set(), set())

        _migrate(database_url, "upgrade", after)
        assert _pairing_schema(plain_url) == paired


def _a_connection(plain_url: str, *, claimed: bool):
    """A connected machine and its row, the way approval leaves them."""
    connection_id, user_id = uuid4(), uuid4()
    name = "claimed" if claimed else "laptop"
    with psycopg.connect(plain_url) as connection:
        connection.execute(
            "INSERT INTO users (id, email, is_verified, is_active, is_superuser, "
            "is_deleted, created_at, updated_at) VALUES "
            "(%s, %s, true, true, false, false, now(), now())",
            (user_id, f"{user_id.hex[:8]}@example.com"),
        )
        connection.execute(
            "INSERT INTO machine_connections (id, user_id, name, display_name, "
            "mode, secret_hash, home, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, 'host', %s, NULL, now(), now())",
            (
                connection_id,
                user_id,
                name,
                name,
                "h" + connection_id.hex if claimed else None,
            ),
        )
        connection.execute(
            "INSERT INTO sandboxes (id, kind, owner_kind, owner_id, slug, "
            "display_name, profile_name, profile_digest, connection_id, "
            "created_at, updated_at) "
            "VALUES (%s, 'workspace', 'user', %s, %s, %s, '', '', %s, now(), now())",
            (connection_id, user_id, name, name, connection_id),
        )
    return connection_id


def _machine_row(plain_url: str, sandbox_id) -> tuple[object, ...]:
    with psycopg.connect(plain_url) as connection:
        return connection.execute(
            "SELECT slug, desired_state FROM sandboxes WHERE id = %s", (sandbox_id,)
        ).fetchone()


def test_rolling_back_pairing_retires_the_machine_of_a_connection_never_claimed() -> (
    None
):
    """Downgrading revokes an approved, unclaimed connection; its machine row
    must give its name back too, or approving that name after a re-upgrade is
    refused with nothing left to expire it."""
    after = "0075_machine_connection_pairing"
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", after)
        unclaimed = _a_connection(plain_url, claimed=False)
        claimed = _a_connection(plain_url, claimed=True)

        _migrate(database_url, "downgrade", REVISION)

        assert _machine_row(plain_url, unclaimed) == (f"~{unclaimed.hex}", "deleted")
        assert _machine_row(plain_url, claimed)[0] == "claimed"
        _migrate(database_url, "upgrade", after)
