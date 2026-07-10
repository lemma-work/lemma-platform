#!/usr/bin/env python3
"""Exercise clean install, downgrade, data repair, and re-upgrade on a test DB."""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import psycopg
from alembic import command
from alembic.config import Config


def _sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def main() -> int:
    url = os.environ.get("MIGRATION_TEST_DATABASE_URL")
    if not url:
        raise RuntimeError("MIGRATION_TEST_DATABASE_URL is required")
    database = urlsplit(_sync_url(url)).path.lstrip("/")
    if "migration_test" not in database:
        raise RuntimeError("Refusing to mutate a database not named migration_test")

    os.environ["DATABASE_URL"] = url
    config = Config("alembic.ini")
    command.upgrade(config, "head")
    command.downgrade(config, "0002_surfaces_rework")

    with psycopg.connect(_sync_url(url)) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO usage_limit_counters (
                organization_id, user_id, window_kind, window_start, window_end,
                used_usd, reserved_usd, id, created_at, updated_at
            ) VALUES
                (NULL, NULL, 'WEEK', '2026-07-06T00:00:00Z',
                 '2026-07-13T00:00:00Z', 1.25, 0.50,
                 '00000000-0000-0000-0000-000000000001', now(), now()),
                (NULL, NULL, 'WEEK', '2026-07-06T00:00:00Z',
                 '2026-07-13T00:00:00Z', 2.75, 0.25,
                 '00000000-0000-0000-0000-000000000002', now(), now())
            """
        )
    command.upgrade(config, "head")

    with psycopg.connect(_sync_url(url)) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT count(*), sum(used_usd), sum(reserved_usd)
            FROM usage_limit_counters
            WHERE organization_id IS NULL AND user_id IS NULL
              AND window_kind = 'WEEK'
            """
        )
        assert cursor.fetchone() == (1, 4.0, 0.75)
        cursor.execute(
            """
            SELECT to_regclass(name) IS NOT NULL
            FROM unnest(ARRAY[
                'domain_event_outbox', 'domain_event_inbox', 'schedule_runs',
                'pod_bundle_jobs', 'pod_bundle_job_steps'
            ]) AS name
            """
        )
        assert all(row[0] for row in cursor.fetchall())
        cursor.execute(
            """
            SELECT consecutive_failures
            FROM schedules
            LIMIT 0
            """
        )
    print("Reliability migration verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
