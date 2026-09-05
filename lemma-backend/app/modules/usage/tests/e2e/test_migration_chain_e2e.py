"""Exercise the deployment migration path, not metadata.create_all()."""

import os
from pathlib import Path
import subprocess

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
MAIN_REVISION = "0022_function_revisions"
USAGE_REVISION = "0030_usage_allocations"


def test_usage_upgrade_follows_app_and_function_history_and_can_roll_back() -> None:
    scripts = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    head = scripts.get_current_head()
    assert head is not None
    assert scripts.get_revision(USAGE_REVISION).down_revision == MAIN_REVISION

    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        environment = {**os.environ, "DATABASE_URL": database_url}

        def migrate(direction: str, revision: str) -> None:
            result = subprocess.run(
                ["uv", "run", "--no-sync", "alembic", direction, revision],
                cwd=BACKEND,
                env=environment,
                capture_output=True,
                text=True,
                timeout=120,
            )
            assert result.returncode == 0, result.stdout + result.stderr

        migrate("upgrade", MAIN_REVISION)
        with psycopg.connect(
            database_url.replace("postgresql+asyncpg", "postgresql")
        ) as connection:
            assert connection.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone() == (MAIN_REVISION,)
            connection.execute(
                "INSERT INTO usage_records (id, created_at, updated_at, user_id, source_type, profile_id, profile_scope, model_name, usage_kind, input_tokens, output_tokens, units, cost_usd, occurred_at) "
                "VALUES (gen_random_uuid(), now(), now(), gen_random_uuid(), 'agent_run', 'legacy', 'SYSTEM', 'legacy', 'LLM', 100, 10, 0, 0.125, now())"
            )

        for direction, revision in (
            ("upgrade", "head"),
            ("downgrade", MAIN_REVISION),
            ("upgrade", "head"),
        ):
            migrate(direction, revision)
            with psycopg.connect(
                database_url.replace("postgresql+asyncpg", "postgresql")
            ) as connection:
                expected = head if revision == "head" else MAIN_REVISION
                assert connection.execute(
                    "SELECT version_num FROM alembic_version"
                ).fetchone() == (expected,)
                assert connection.execute(
                    "SELECT cost_usd FROM usage_records WHERE profile_id = 'legacy'"
                ).fetchone() == (0.125,)
                assert connection.execute(
                    "SELECT to_regclass('app_releases'), to_regclass('function_revisions')"
                ).fetchone() == ("app_releases", "function_revisions")
                allocation_table = connection.execute(
                    "SELECT to_regclass('usage_allocations')"
                ).fetchone()[0]
                assert allocation_table == (
                    "usage_allocations" if revision == "head" else None
                )
