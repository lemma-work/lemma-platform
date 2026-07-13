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
    command.upgrade(config, "0005_identity_normalization")

    with psycopg.connect(_sync_url(url)) as connection, connection.cursor() as cursor:
        cursor.execute(
            """
            INSERT INTO users (
                email, is_verified, is_active, is_superuser, is_deleted,
                id, created_at, updated_at
            ) VALUES (
                'schedule-migration@example.com', true, true, false, false,
                '00000000-0000-0000-0000-000000000010', now(), now()
            );
            INSERT INTO organizations (
                name, slug, join_policy, id, created_at, updated_at
            ) VALUES (
                'Schedule Migration', 'schedule-migration', 'INVITE_ONLY',
                '00000000-0000-0000-0000-000000000020', now(), now()
            );
            INSERT INTO pods (
                user_id, organization_id, name, config, is_deleted,
                id, created_at, updated_at
            ) VALUES (
                '00000000-0000-0000-0000-000000000010',
                '00000000-0000-0000-0000-000000000020',
                'Schedule Migration Pod', '{}', false,
                '00000000-0000-0000-0000-000000000030', now(), now()
            );
            INSERT INTO workflow_flows (
                pod_id, user_id, name, nodes, edges, start, mode, is_active,
                visibility, id, created_at, updated_at
            ) VALUES (
                '00000000-0000-0000-0000-000000000030',
                '00000000-0000-0000-0000-000000000010',
                'schedule-migration-flow', '[]', '[]', '{}', 'GLOBAL', true,
                'POD', '00000000-0000-0000-0000-000000000040', now(), now()
            );
            INSERT INTO schedules (
                user_id, pod_id, name, schedule_type, workflow_id, config,
                visibility, is_active, is_internal, consecutive_failures,
                id, created_at, updated_at
            ) VALUES (
                '00000000-0000-0000-0000-000000000010',
                '00000000-0000-0000-0000-000000000030',
                'schedule-migration-ledger', 'DATASTORE',
                '00000000-0000-0000-0000-000000000040', '{}', 'POD', true,
                false, 0, '00000000-0000-0000-0000-000000000050', now(), now()
            );
            INSERT INTO workflow_flow_runs (
                flow_id, pod_id, user_id, start_type, start_payload, status,
                execution_context, execution_stack, step_history, completed_at,
                id, created_at, updated_at
            ) VALUES
                (
                    '00000000-0000-0000-0000-000000000040',
                    '00000000-0000-0000-0000-000000000030',
                    '00000000-0000-0000-0000-000000000010',
                    'DATASTORE_EVENT', '{}', 'FAILED', '{}', '[]', '[]',
                    now() - interval '4 minutes',
                    '00000000-0000-0000-0000-000000000061',
                    now() - interval '4 minutes', now() - interval '4 minutes'
                ),
                (
                    '00000000-0000-0000-0000-000000000040',
                    '00000000-0000-0000-0000-000000000030',
                    '00000000-0000-0000-0000-000000000010',
                    'DATASTORE_EVENT', '{}', 'COMPLETED', '{}', '[]', '[]',
                    now() - interval '3 minutes',
                    '00000000-0000-0000-0000-000000000062',
                    now() - interval '3 minutes', now() - interval '3 minutes'
                );
            INSERT INTO agent_conversations (
                user_id, pod_id, conversation_type, status, origin_type,
                origin_id, id, created_at, updated_at
            ) VALUES (
                '00000000-0000-0000-0000-000000000010',
                '00000000-0000-0000-0000-000000000030',
                'WORKFLOW', 'FAILED', 'SCHEDULE_RUN',
                '00000000-0000-0000-0000-000000000073',
                '00000000-0000-0000-0000-000000000063',
                now() - interval '2 minutes', now() - interval '2 minutes'
            );
            INSERT INTO schedule_runs (
                schedule_id, source_event_id, status, attempts, target_kind,
                target_run_id, payload, metadata, llm_output, completed_at,
                id, created_at, updated_at
            ) VALUES
                (
                    '00000000-0000-0000-0000-000000000050', 'failed-workflow',
                    'DISPATCHED', 1, 'WORKFLOW',
                    '00000000-0000-0000-0000-000000000061', '{}', '{}', '{}',
                    now() - interval '4 minutes',
                    '00000000-0000-0000-0000-000000000071',
                    now() - interval '4 minutes', now() - interval '4 minutes'
                ),
                (
                    '00000000-0000-0000-0000-000000000050', 'completed-workflow',
                    'DISPATCHED', 1, 'WORKFLOW',
                    '00000000-0000-0000-0000-000000000062', '{}', '{}', '{}',
                    now() - interval '3 minutes',
                    '00000000-0000-0000-0000-000000000072',
                    now() - interval '3 minutes', now() - interval '3 minutes'
                ),
                (
                    '00000000-0000-0000-0000-000000000050', 'failed-agent',
                    'DISPATCHED', 1, 'AGENT',
                    '00000000-0000-0000-0000-000000000063', '{}', '{}', '{}',
                    now() - interval '2 minutes',
                    '00000000-0000-0000-0000-000000000073',
                    now() - interval '2 minutes', now() - interval '2 minutes'
                ),
                (
                    '00000000-0000-0000-0000-000000000050', 'dead-letter',
                    'DEAD_LETTERED', 10, 'WORKFLOW', NULL, '{}', '{}', '{}',
                    now() - interval '1 minute',
                    '00000000-0000-0000-0000-000000000074',
                    now() - interval '1 minute', now() - interval '1 minute'
                ),
                (
                    '00000000-0000-0000-0000-000000000050', 'retryable-failure',
                    'FAILED', 2, 'WORKFLOW', NULL, '{}', '{}', '{}', now(),
                    '00000000-0000-0000-0000-000000000075', now(), now()
                );
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
            WHERE id = '00000000-0000-0000-0000-000000000050'
            """
        )
        assert cursor.fetchone() == (2,)
        cursor.execute(
            """
            SELECT source_event_id, status, user_id::text,
                   target_run_id IS NOT NULL
            FROM schedule_runs
            WHERE schedule_id = '00000000-0000-0000-0000-000000000050'
            ORDER BY source_event_id
            """
        )
        assert cursor.fetchall() == [
            (
                "completed-workflow",
                "COMPLETED",
                "00000000-0000-0000-0000-000000000010",
                True,
            ),
            (
                "dead-letter",
                "DEAD_LETTERED",
                "00000000-0000-0000-0000-000000000010",
                True,
            ),
            (
                "failed-agent",
                "TARGET_FAILED",
                "00000000-0000-0000-0000-000000000010",
                True,
            ),
            (
                "failed-workflow",
                "TARGET_FAILED",
                "00000000-0000-0000-0000-000000000010",
                True,
            ),
            (
                "retryable-failure",
                "FAILED",
                "00000000-0000-0000-0000-000000000010",
                True,
            ),
        ]
        cursor.execute(
            """
            SELECT origin_type, origin_id
            FROM agent_conversations
            WHERE id = '00000000-0000-0000-0000-000000000063'
            """
        )
        assert cursor.fetchone() == (None, None)
        cursor.execute(
            """
            SELECT
                to_regclass('uq_schedule_runs_target') IS NOT NULL,
                to_regclass('ix_schedule_runs_retryable_recovery') IS NULL,
                is_nullable
            FROM information_schema.columns
            WHERE table_name = 'schedule_runs'
              AND column_name = 'target_run_id'
            """
        )
        assert cursor.fetchone() == (True, True, "NO")
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
    print("Reliability migration verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
