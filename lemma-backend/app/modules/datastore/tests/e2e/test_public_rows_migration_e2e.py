"""The open-table grant and its two columns come and go with their migration."""

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0044_contacts"
AFTER = "0045_public_rows"


def test_the_open_table_schema_upgrades_and_rolls_back_cleanly() -> None:
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
                    "SELECT to_regclass('datastore_public_rows'), "
                    "to_regclass('ix_datastore_public_rows_opened_by'), "
                    "(SELECT count(*) FROM information_schema.columns "
                    " WHERE (table_name, column_name) IN ("
                    "  ('datastore_tables', 'contact_columns'), "
                    "  ('schedules', 'include_outside_rows')))"
                ).fetchone()
                assert row is not None
                return (row[0] is not None, row[1] is not None, row[2])

        migrate("upgrade", BEFORE)
        assert present() == (False, False, 0)

        migrate("upgrade", AFTER)
        assert present() == (True, True, 2)

        migrate("downgrade", BEFORE)
        assert present() == (False, False, 0)

        migrate("upgrade", "head")
        assert present() == (True, True, 2)
