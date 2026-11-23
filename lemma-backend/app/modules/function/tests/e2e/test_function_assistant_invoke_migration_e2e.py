"""0075 adds ``functions.assistant_invoke`` (every existing function stays
offered to the assistant), rolls back, and reruns cleanly."""

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

# Walking the Alembic chain from the base is slow; nightly protected lane, like
# the other single-revision migration tests.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0074_machines"
REVISION = "0075_function_assistant_invoke"

_COLUMN = (
    "SELECT data_type, is_nullable, column_default FROM information_schema.columns "
    "WHERE table_name = 'functions' AND column_name = 'assistant_invoke'"
)


def test_the_column_is_added_with_every_function_offered_and_rolls_back() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        environment = {**os.environ, "DATABASE_URL": database_url}
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")

        def migrate(direction: str, revision: str) -> None:
            result = subprocess.run(
                ["uv", "run", "--no-sync", "alembic", direction, revision],
                cwd=BACKEND,
                env=environment,
                capture_output=True,
                text=True,
                timeout=300,
            )
            assert result.returncode == 0, result.stdout + result.stderr

        def column() -> tuple[str, str, str] | None:
            with psycopg.connect(sync_url) as connection:
                return connection.execute(_COLUMN).fetchone()

        migrate("upgrade", BEFORE)
        assert column() is None

        migrate("upgrade", REVISION)
        added = column()
        assert added is not None
        assert added[0] == "boolean" and added[1] == "NO" and added[2] == "true"

        migrate("downgrade", BEFORE)
        assert column() is None

        # Reruns cleanly over a column already there (IF NOT EXISTS).
        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url, autocommit=True) as connection:
            connection.execute("UPDATE alembic_version SET version_num = %s", (BEFORE,))
        migrate("upgrade", REVISION)
        assert column() is not None
