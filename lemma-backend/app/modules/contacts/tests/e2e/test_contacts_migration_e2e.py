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

        def beside() -> tuple[bool, bool]:
            """The link index and the notifications column the revision adds."""
            with psycopg.connect(plain_url) as connection:
                row = connection.execute(
                    "SELECT to_regclass('ix_agent_surface_link_external_user') "
                    "IS NOT NULL, EXISTS (SELECT 1 FROM information_schema.columns "
                    "WHERE table_name = 'notifications' "
                    "AND column_name = 'asked_in_private')"
                ).fetchone()
                assert row is not None
                return bool(row[0]), bool(row[1])

        def cap_goes_with_its_organization() -> bool:
            with psycopg.connect(plain_url) as connection:
                row = connection.execute(
                    "SELECT confdeltype FROM pg_constraint WHERE contype = 'f' "
                    "AND conrelid = 'usage_contacts_caps'::regclass "
                    "AND confrelid = 'organizations'::regclass"
                ).fetchone()
                return row is not None and row[0] == "c"

        absent = (None,) * len(TABLES)

        migrate("upgrade", BEFORE)
        assert present() == absent
        assert beside() == (False, False)

        migrate("upgrade", AFTER)
        assert present() == TABLES
        assert beside() == (True, True)
        assert cap_goes_with_its_organization()

        migrate("downgrade", BEFORE)
        assert present() == absent
        assert beside() == (False, False)

        migrate("upgrade", "head")
        assert present() == TABLES
