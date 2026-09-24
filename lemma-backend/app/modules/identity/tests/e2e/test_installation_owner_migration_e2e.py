"""0040 applies, rolls back and re-applies through Alembic, not create_all()."""

import os
import subprocess
from pathlib import Path

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
PREVIOUS = "0039_whatsapp_number_pool"
REVISION = "0040_installation_owner"


def test_installation_owner_upgrades_downgrades_and_holds_one_row() -> None:
    scripts = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert scripts.get_revision(REVISION).down_revision == PREVIOUS

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

        def table_exists() -> bool:
            with psycopg.connect(plain_url) as connection:
                row = connection.execute(
                    "SELECT to_regclass('installation_owner')"
                ).fetchone()
            return row is not None and row[0] is not None

        migrate("upgrade", REVISION)
        assert table_exists()
        with psycopg.connect(plain_url) as connection:
            connection.execute(
                "INSERT INTO installation_owner (email, reserved_at) "
                "VALUES ('me@example.com', now())"
            )
            # The singleton key is the race guarantee; a second row -- or a
            # row that tries to be a different singleton -- must be refused.
            with pytest.raises(psycopg.errors.UniqueViolation):
                connection.execute(
                    "INSERT INTO installation_owner (email, reserved_at) "
                    "VALUES ('someone@example.com', now())"
                )
        with psycopg.connect(plain_url) as connection:
            with pytest.raises(psycopg.errors.CheckViolation):
                connection.execute(
                    "INSERT INTO installation_owner (singleton, email, reserved_at) "
                    "VALUES (false, 'someone@example.com', now())"
                )

        migrate("downgrade", PREVIOUS)
        assert not table_exists()
        migrate("upgrade", "head")
        assert table_exists()
