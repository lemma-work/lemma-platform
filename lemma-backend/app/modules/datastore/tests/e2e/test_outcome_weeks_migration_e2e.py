"""Migration 0066 adds the week rows of what a pod owns, and the sweep's index.

A pod already there when it runs is untouched: its config is not read or
rewritten, and the index covers a pod only once its outcome has a measure. The
week rows go with their pod. The downgrade drops both, and upgrading again
works.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

# Walks the Alembic chain from the base on its own Postgres: real upgrade,
# downgrade and upgrade again, which cannot be faked and costs 15-30s on a CI
# runner. Like the other chain walks it runs in the nightly protected lane; the
# pull-request lane keeps one round trip, `test_work_migration_e2e.py`.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0065_schedule_reset_and_author"
REVISION = "0066_outcome_weeks"

MEASURED = {
    "outcome": {
        "sentence": "Two posts go out every week.",
        "kind": "week",
        "measure": "SELECT count(*) FROM posts",
    },
    "join_policy": "ORG_MEMBERS",
}


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


def _pods(plain_url: str) -> dict[str, UUID]:
    """Two pods written before 0066: one whose config says it owns a measured
    outcome, and one that owns nothing."""
    org, owner = uuid4(), uuid4()
    pods = {"measured": uuid4(), "plain": uuid4()}
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        run(
            "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
            "updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', now(), now())",
            (org, f"org-{org.hex[:8]}"),
        )
        run(
            "INSERT INTO users (id, email, is_verified, is_active, is_superuser, "
            "is_deleted, created_at, updated_at) "
            "VALUES (%s, %s, true, true, false, false, now(), now())",
            (owner, f"{owner.hex[:8]}@example.com"),
        )
        for name, config in (("measured", MEASURED), ("plain", {})):
            run(
                "INSERT INTO pods (id, user_id, organization_id, name, config, "
                "is_deleted, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, false, now(), now())",
                (pods[name], owner, org, name, json.dumps(config)),
            )
    return pods


def _tables(plain_url: str) -> set[str]:
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            "SELECT tablename FROM pg_tables WHERE tablename = 'pod_outcome_weeks' "
            "UNION SELECT indexname FROM pg_indexes WHERE indexname = 'ix_pods_with_measure'"
        ).fetchall()
    return {row[0] for row in rows}


def _configs(plain_url: str, pods: dict[str, UUID]) -> dict[str, object]:
    by_id = {pod_id: name for name, pod_id in pods.items()}
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute("SELECT id, config FROM pods").fetchall()
    return {by_id[pod_id]: config for pod_id, config in rows}


def _measured_pods(plain_url: str) -> list[UUID]:
    """What the sweep's statement reads, as the planner is made to use the index."""
    with psycopg.connect(plain_url) as connection:
        connection.execute("SET enable_seqscan = off")
        rows = connection.execute(
            "SELECT id FROM pods WHERE (config -> 'outcome' ->> 'measure') IS NOT NULL "
            "AND NOT is_deleted ORDER BY id"
        ).fetchall()
    return [row[0] for row in rows]


def _a_week(plain_url: str, pod_id: UUID) -> None:
    with psycopg.connect(plain_url) as connection:
        connection.execute(
            "INSERT INTO pod_outcome_weeks (pod_id, starts_on, measure_digest, "
            "value, target, measured_at) VALUES (%s, '2026-10-19', %s, 2, 2, now())",
            (pod_id, "0" * 64),
        )


def _weeks(plain_url: str) -> int:
    with psycopg.connect(plain_url) as connection:
        (count,) = connection.execute(
            "SELECT count(*) FROM pod_outcome_weeks"
        ).fetchone() or (0,)
    return int(count)


def test_the_week_rows_arrive_empty_leave_pods_alone_and_roll_back() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        assert _tables(plain_url) == set()
        pods = _pods(plain_url)
        configs = _configs(plain_url, pods)

        _migrate(database_url, "upgrade", REVISION)
        assert _tables(plain_url) == {"pod_outcome_weeks", "ix_pods_with_measure"}
        assert _configs(plain_url, pods) == configs, "no pod's config is touched"
        assert _measured_pods(plain_url) == [pods["measured"]]
        assert _weeks(plain_url) == 0

        _a_week(plain_url, pods["measured"])
        with psycopg.connect(plain_url) as connection:
            connection.execute("DELETE FROM pods WHERE id = %s", (pods["measured"],))
        assert _weeks(plain_url) == 0, "a pod's weeks go with it"

        _migrate(database_url, "downgrade", BEFORE)
        assert _tables(plain_url) == set()

        _migrate(database_url, "upgrade", REVISION)
        assert _tables(plain_url) == {"pod_outcome_weeks", "ix_pods_with_measure"}
