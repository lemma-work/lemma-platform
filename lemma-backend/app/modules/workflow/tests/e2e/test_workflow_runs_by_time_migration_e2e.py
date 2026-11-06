"""0072 builds the index a pod's workflow runs are read by time through, rolls
back, and recovers from its own interrupted build.

``CREATE INDEX CONCURRENTLY`` that fails part way leaves the index behind,
marked invalid. ``IF NOT EXISTS`` then skips it on the rerun, the revision is
stamped, and the planner never uses it. The rerun must drop the invalid index
and build it again.
"""

import os
import subprocess
from pathlib import Path

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

# Walking the Alembic chain from the base costs every one of these tests
# ten-odd seconds, so they run in the nightly protected lane. The pull-request
# lane keeps one round trip, `app/core/tests/e2e/test_work_migration_e2e.py`.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0071_outcome_weeks"
REVISION = "0072_workflow_runs_by_time"
INDEX = "ix_workflow_flow_runs_pod_created"

_VALID = (
    "SELECT i.indisvalid FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
    "WHERE c.relname = %s"
)
_DEFINITION = "SELECT indexdef FROM pg_indexes WHERE indexname = %s"


def test_the_index_is_built_rolls_back_and_recovers_from_an_interrupted_build() -> None:
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

        def definition() -> str | None:
            with psycopg.connect(sync_url) as connection:
                row = connection.execute(_DEFINITION, (INDEX,)).fetchone()
            return row[0] if row else None

        migrate("upgrade", BEFORE)
        assert definition() is None

        migrate("upgrade", REVISION)
        built = definition()
        assert built is not None and "(pod_id, created_at)" in built

        migrate("downgrade", BEFORE)
        assert definition() is None

        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url, autocommit=True) as connection:
            assert connection.execute(_VALID, (INDEX,)).fetchone() == (True,)
            # What an interrupted concurrent build leaves: the name, invalid,
            # and the revision not yet stamped.
            connection.execute(
                "UPDATE pg_index SET indisvalid = false "
                "WHERE indexrelid = %s::regclass",
                (INDEX,),
            )
            connection.execute("UPDATE alembic_version SET version_num = %s", (BEFORE,))

        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert connection.execute(_VALID, (INDEX,)).fetchone() == (True,), (
                "the rerun should have dropped the invalid index and built it again"
            )
