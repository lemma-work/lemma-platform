#!/usr/bin/env python3
"""Exercise clean install, downgrade, data repair, and re-upgrade on a test DB."""

from __future__ import annotations

import os
from urllib.parse import urlsplit
from uuid import UUID

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
    command.upgrade(config, "0007_auth_hardening")

    with psycopg.connect(_sync_url(url)) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO users (
                email, is_verified, is_active, is_superuser, is_deleted,
                mobile_number, mobile_verified_at, id, created_at, updated_at
            ) VALUES
                ('legacy-mobile-loser@example.com', true, true, false, false,
                 '+1 (415) 555-2671', NULL,
                 '10000000-0000-0000-0000-000000000001',
                 '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z'),
                ('legacy-mobile-owner@example.com', true, true, false, false,
                 '14155552671', '2026-02-01T00:00:00Z',
                 '10000000-0000-0000-0000-000000000002',
                 '2026-02-01T00:00:00Z', '2026-02-01T00:00:00Z'),
                ('unique-mobile@example.com', true, true, false, false,
                 '+44 7700 900123', NULL,
                 '10000000-0000-0000-0000-000000000003',
                 '2026-03-01T00:00:00Z', '2026-03-01T00:00:00Z')
            """
        )
        cursor.execute(
            """
            INSERT INTO agent_surface_external_users (
                platform, tenant_id, external_user_id, raw_profile,
                resolved_user_id, id, created_at, updated_at
            ) VALUES (
                'whatsapp', 'legacy-test', '14155552671', '{}'::jsonb,
                '10000000-0000-0000-0000-000000000001',
                '20000000-0000-0000-0000-000000000001', now(), now()
            )
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
        cursor.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'datastore_files'
              AND column_name IN (
                  'content_sha256', 'storage_key', 'content_revision',
                  'processing_phase', 'processing_started_at'
              )
            ORDER BY column_name
            """
        )
        assert cursor.fetchall() == [("content_sha256",)]
        cursor.execute(
            """
            SELECT id, mobile_number, mobile_verified_at IS NOT NULL
            FROM users
            WHERE id IN (
                '10000000-0000-0000-0000-000000000001',
                '10000000-0000-0000-0000-000000000002'
            )
            ORDER BY id
            """
        )
        assert cursor.fetchall() == [
            (
                UUID("10000000-0000-0000-0000-000000000001"),
                None,
                False,
            ),
            (
                UUID("10000000-0000-0000-0000-000000000002"),
                "14155552671",
                True,
            ),
        ]
        cursor.execute(
            """
            SELECT resolved_user_id
            FROM agent_surface_external_users
            WHERE id = '20000000-0000-0000-0000-000000000001'
            """
        )
        assert cursor.fetchone() == (None,)
        cursor.execute(
            """
            SELECT indexdef
            FROM pg_indexes
            WHERE schemaname = current_schema()
              AND tablename = 'users'
              AND indexname = 'uq_users_mobile_number_digits'
            """
        )
        index_definition = cursor.fetchone()
        assert index_definition is not None
        assert "regexp_replace" in index_definition[0]

    try:
        with (
            psycopg.connect(_sync_url(url)) as connection,
            connection.cursor() as cursor,
        ):
            cursor.execute(
                """
                UPDATE users
                SET mobile_number = '+1 415-555-2671'
                WHERE id = '10000000-0000-0000-0000-000000000003'
                """
            )
    except psycopg.errors.UniqueViolation:
        pass
    else:
        raise AssertionError("Normalized duplicate mobile number was accepted")
    print("Reliability migration verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
