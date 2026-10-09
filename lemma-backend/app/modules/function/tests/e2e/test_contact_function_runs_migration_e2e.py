"""A run with no member exists only above 0044, and rolling back removes it."""

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0043_surface_groups"
AFTER = "0044_contacts"


def test_function_runs_take_a_contact_and_roll_back_cleanly() -> None:
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

        def shape() -> tuple[object, ...]:
            with psycopg.connect(plain_url) as connection:
                row = connection.execute(
                    "SELECT "
                    "(SELECT is_nullable FROM information_schema.columns "
                    " WHERE table_name = 'function_runs' AND column_name = 'user_id'), "
                    "(SELECT count(*) FROM information_schema.columns "
                    " WHERE table_name = 'function_runs' "
                    " AND column_name = 'contact_id'), "
                    "to_regclass('ix_function_runs_contact_id') IS NOT NULL"
                ).fetchone()
                assert row is not None
                return tuple(row)

        migrate("upgrade", BEFORE)
        assert shape() == ("NO", 0, False)

        migrate("upgrade", AFTER)
        assert shape() == ("YES", 1, True)
        with psycopg.connect(plain_url) as connection:
            connection.execute(
                "INSERT INTO function_runs (id, created_at, updated_at, status) "
                "VALUES (%s, now(), now(), 'CANCELLED')",
                (uuid4(),),
            )

        # The run with no member cannot survive ``user_id`` being required again.
        migrate("downgrade", BEFORE)
        assert shape() == ("NO", 0, False)
        with psycopg.connect(plain_url) as connection:
            row = connection.execute("SELECT count(*) FROM function_runs").fetchone()
        assert row == (0,)

        migrate("upgrade", "head")
        assert shape() == ("YES", 1, True)
