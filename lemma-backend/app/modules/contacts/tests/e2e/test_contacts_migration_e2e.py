"""The contact tables come and go with their migration, and only with it."""

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0043_surface_groups"
AFTER = "0044_contacts"
TABLES = (
    "contacts",
    "contact_identities",
    "usage_contacts_caps",
    "agent_surface_web_widgets",
    "visitor_sessions",
    "agent_surface_web_codes",
)


def test_the_contact_tables_upgrade_and_roll_back_cleanly() -> None:
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

        def present() -> tuple[object, ...]:
            with psycopg.connect(plain_url) as connection:
                row = connection.execute(
                    "SELECT " + ", ".join(f"to_regclass('{table}')" for table in TABLES)
                ).fetchone()
                assert row is not None
                return tuple(row)

        absent = (None,) * len(TABLES)

        migrate("upgrade", BEFORE)
        assert present() == absent

        migrate("upgrade", AFTER)
        assert present() == TABLES

        migrate("downgrade", BEFORE)
        assert present() == absent

        migrate("upgrade", "head")
        assert present() == TABLES
