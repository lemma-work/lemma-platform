"""The agent run reliability migration: up, down and up again, data included.

Seeded at the revision before it with every shape the migration repairs, then
checked after each step against a real Postgres -- the repairs are SQL over
JSON, casing and foreign keys, which only the database itself can answer for.
"""

import json
import os
import subprocess
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory

from app.core.test_utils import get_postgres_container, get_postgres_url

# Walking the Alembic chain from the base costs every one of these tests
# ten-odd seconds, so they run in the weekly protected lane. The pull-request
# lane keeps one round trip, `app/core/tests/e2e/test_work_migration_e2e.py`.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0050_pod_grantee_index"
REVISION = "0051_agent_run_reliability"
_RUN_CAP_CHECK = "ck_workflow_flows_max_concurrent_runs_positive"


class _Ids:
    def __init__(self) -> None:
        for name in (
            "org",
            "member",
            "leaver",
            "pod",
            "older_default",
            "newer_default",
            "orphan",
            "harness_profile",
            "leavers_profile",
            "leavers_host",
            "leavers_harness",
            "handle_binding",
            "phone_binding",
            "conversation",
            "run",
            "message",
            "decision",
            "flow",
        ):
            setattr(self, name, uuid4())


def _seed(connection: psycopg.Connection, ids: _Ids) -> None:
    run = connection.execute
    run(
        "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
        "updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', now(), now())",
        (ids.org, f"org-{ids.org.hex[:8]}"),
    )
    for user_id, handle in ((ids.member, "squatted_handle"), (ids.leaver, None)):
        run(
            "INSERT INTO users (id, email, is_verified, is_active, is_superuser, "
            "is_deleted, telegram_username, created_at, updated_at) "
            "VALUES (%s, %s, true, true, false, false, %s, now(), now())",
            (user_id, f"{user_id.hex[:8]}@example.com", handle),
        )
    run(
        "INSERT INTO pods (id, user_id, organization_id, name, config, is_deleted, "
        "created_at, updated_at) VALUES (%s, %s, %s, 'Pod', '{}', false, now(), "
        "now())",
        (ids.pod, ids.member, ids.org),
    )
    run(
        "INSERT INTO agent_hosts (id, user_id, installation_id, host_secret_hash, "
        "display_name, status, host_release, capacity, created_at, updated_at) "
        "VALUES (%s, %s, 'install', 'hash', 'Mac', 'OFFLINE', '1', '{}', now(), "
        "now())",
        (ids.leavers_host, ids.leaver),
    )
    run(
        "INSERT INTO agent_host_harnesses (id, host_id, harness_key, display_name, "
        "adapter_version, health, capabilities, config_revision, config_options, "
        "stale_after, created_at, updated_at) VALUES (%s, %s, 'claude-code', "
        "'Claude Code', '1', 'HEALTHY', '{}', 'r1', '[]', now(), now(), now())",
        (ids.leavers_harness, ids.leavers_host),
    )

    def profile(
        profile_id: UUID,
        *,
        scope: str,
        kind: str,
        user_id: UUID | None = None,
        config: dict[str, object] | None = None,
        metadata: dict[str, object] | None = None,
        updated: str = "now()",
        harness_id: UUID | None = None,
    ) -> None:
        run(
            "INSERT INTO agent_runtime_profiles (id, organization_id, user_id, "
            "scope, kind, protocol, name, model_catalog, config, status, "
            "profile_metadata, runtime_type, harness_id, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, %s, 'OPENAI_COMPATIBLE', %s, '[]', %s, "
            f"'ACTIVE', %s, %s, %s, now(), {updated})",
            (
                profile_id,
                ids.org,
                user_id,
                scope,
                kind,
                f"profile-{profile_id.hex[:8]}",
                json.dumps(config or {}),
                json.dumps(metadata or {}),
                "HARNESS" if harness_id else None,
                harness_id,
            ),
        )

    mark = {"organization_default": {"model_name": None}}
    profile(
        ids.older_default,
        scope="ORGANIZATION",
        kind="MODEL_PROVIDER",
        metadata=mark,
        updated="now() - interval '1 day'",
    )
    profile(
        ids.newer_default, scope="ORGANIZATION", kind="MODEL_PROVIDER", metadata=mark
    )
    profile(ids.orphan, scope="PERSONAL", kind="MODEL_PROVIDER")
    profile(
        ids.harness_profile,
        scope="ORGANIZATION",
        kind="HARNESS",
        config={"host_wait_timeout_seconds": None, "keep": 1},
    )
    profile(
        ids.leavers_profile,
        scope="PERSONAL",
        kind="HARNESS",
        user_id=ids.leaver,
        harness_id=ids.leavers_harness,
    )

    for binding, phone in (
        (ids.handle_binding, None),
        (ids.phone_binding, "15550001111"),
    ):
        run(
            "INSERT INTO agent_surface_external_users (id, platform, "
            "external_user_id, raw_profile, resolved_user_id, phone, created_at, "
            "updated_at) VALUES (%s, 'TELEGRAM', %s, '{}', %s, %s, now(), now())",
            (binding, binding.hex[:12], ids.member, phone),
        )

    run(
        "INSERT INTO agent_conversations (id, user_id, pod_id, conversation_type, "
        "status, created_at, updated_at) VALUES (%s, %s, %s, 'CHAT', 'running', "
        "now(), now())",
        (ids.conversation, ids.member, ids.pod),
    )
    run(
        "INSERT INTO agent_runs (id, conversation_id, status, agent_runtime, "
        "started_at, created_at, updated_at) VALUES (%s, %s, 'completed', '{}', "
        "now(), now(), now())",
        (ids.run, ids.conversation),
    )
    run(
        "INSERT INTO agent_messages (id, conversation_id, sequence, role, kind, "
        "created_at) VALUES (%s, %s, 0, 'user', 'text', now())",
        (ids.message, ids.conversation),
    )
    run(
        "INSERT INTO agent_approval_decisions (id, conversation_id, approval_id, "
        "decision, created_at) VALUES (%s, %s, 'approval-1', 'deny', now())",
        (ids.decision, ids.conversation),
    )
    run(
        "INSERT INTO workflow_flows (id, pod_id, name, nodes, edges, mode, "
        "is_active, visibility, created_at, updated_at) VALUES (%s, %s, 'flow', "
        "'[]', '[]', 'GLOBAL', true, 'POD', now(), now())",
        (ids.flow, ids.pod),
    )


def _columns(connection: psycopg.Connection, table: str) -> set[str]:
    rows = connection.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_name = %s",
        (table,),
    ).fetchall()
    return {row[0] for row in rows}


def _index_exists(connection: psycopg.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM pg_class WHERE relname = %s AND relkind = 'i'", (name,)
        ).fetchone()
        is not None
    )


def _constraint_exists(connection: psycopg.Connection, name: str) -> bool:
    return (
        connection.execute(
            "SELECT 1 FROM pg_constraint WHERE conname = %s", (name,)
        ).fetchone()
        is not None
    )


def _profile_user_fk_action(connection: psycopg.Connection) -> str:
    (action,) = connection.execute(
        "SELECT con.confdeltype FROM pg_constraint AS con "
        "JOIN pg_attribute AS att ON att.attrelid = con.conrelid "
        "AND att.attnum = ANY (con.conkey) "
        "WHERE con.conrelid = 'agent_runtime_profiles'::regclass "
        "AND con.contype = 'f' AND att.attname = 'user_id'"
    ).fetchone()
    return action  # 'c' cascade, 'n' set null


def _scalar(connection: psycopg.Connection, sql: str, *args: object) -> object:
    row = connection.execute(sql, args).fetchone()
    return row[0] if row else None


@contextmanager
def _database() -> Iterator[tuple[str, dict[str, str]]]:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        yield (
            database_url.replace("postgresql+asyncpg", "postgresql"),
            {**os.environ, "DATABASE_URL": database_url},
        )


def test_the_migration_repairs_its_data_and_rolls_back_its_schema() -> None:
    scripts = ScriptDirectory.from_config(Config(str(BACKEND / "alembic.ini")))
    assert scripts.get_revision(REVISION).down_revision == BEFORE

    with _database() as (sync_url, environment):

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

        ids = _Ids()
        migrate("upgrade", BEFORE)
        with psycopg.connect(sync_url) as connection:
            _seed(connection, ids)

        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url, autocommit=True) as connection:
            q = connection
            # Run resume.
            assert {"consumed_stream_id", "credential_expires_at"} <= _columns(
                q, "agent_host_run_leases"
            )
            # Telegram: a binding with no number is undone; a proven one stays.
            bound = dict(
                q.execute(
                    "SELECT id, resolved_user_id FROM agent_surface_external_users"
                ).fetchall()
            )
            assert bound[ids.handle_binding] is None
            assert bound[ids.phone_binding] == ids.member
            assert "proven_by" not in _columns(q, "agent_surface_external_users")
            assert (
                _scalar(
                    q, "SELECT count(*) FROM users WHERE telegram_username IS NOT NULL"
                )
                == 0
            )
            assert not _index_exists(q, "ix_users_telegram_username")
            # No index for client message ids: the conversation lock holds it.
            assert not _index_exists(q, "uq_agent_message_client_message_id")
            # Workflow concurrency cap, and its check. Nullable with no default:
            # every existing workflow stays unlimited.
            assert q.execute(
                "SELECT is_nullable, column_default FROM information_schema.columns "
                "WHERE table_name = 'workflow_flows' "
                "AND column_name = 'max_concurrent_runs'"
            ).fetchone() == ("YES", None)
            assert (
                _scalar(
                    q,
                    "SELECT max_concurrent_runs FROM workflow_flows WHERE id = %s",
                    ids.flow,
                )
                is None
            )
            with pytest.raises(psycopg.errors.CheckViolation):
                q.execute(
                    "UPDATE workflow_flows SET max_concurrent_runs = 0 WHERE id = %s",
                    (ids.flow,),
                )
            # Runtime profiles.
            assert (
                _scalar(
                    q,
                    "SELECT version FROM agent_runtime_profiles WHERE id = %s",
                    ids.newer_default,
                )
                == 0
            )
            marked = {
                row[0]
                for row in q.execute(
                    "SELECT id FROM agent_runtime_profiles "
                    "WHERE profile_metadata ? 'organization_default'"
                ).fetchall()
            }
            assert marked == {ids.newer_default}
            with pytest.raises(psycopg.errors.UniqueViolation):
                q.execute(
                    "UPDATE agent_runtime_profiles SET profile_metadata = "
                    "profile_metadata || '{\"organization_default\": {}}' "
                    "WHERE id = %s",
                    (ids.older_default,),
                )
            assert (
                _scalar(
                    q, "SELECT 1 FROM agent_runtime_profiles WHERE id = %s", ids.orphan
                )
                is None
            )
            assert _scalar(
                q,
                "SELECT config FROM agent_runtime_profiles WHERE id = %s",
                ids.harness_profile,
            ) == {"keep": 1}
            assert _profile_user_fk_action(q) == "c"
            # Statuses are capitals now.
            assert (
                _scalar(
                    q,
                    "SELECT status FROM agent_conversations WHERE id = %s",
                    ids.conversation,
                )
                == "RUNNING"
            )
            assert (
                _scalar(q, "SELECT status FROM agent_runs WHERE id = %s", ids.run)
                == "COMPLETED"
            )
            message = q.execute(
                "SELECT kind, role FROM agent_messages WHERE id = %s", (ids.message,)
            ).fetchone()
            assert message == ("TEXT", "user")  # roles stay lowercase by design
            assert (
                _scalar(
                    q,
                    "SELECT decision FROM agent_approval_decisions WHERE id = %s",
                    ids.decision,
                )
                == "DENY"
            )
            # A person who leaves takes their personal profile with them, even
            # one bound to a coding agent on their own (also departing) Mac.
            q.execute("DELETE FROM users WHERE id = %s", (ids.leaver,))
            assert (
                _scalar(
                    q,
                    "SELECT 1 FROM agent_runtime_profiles WHERE id = %s",
                    ids.leavers_profile,
                )
                is None
            )

        migrate("downgrade", BEFORE)
        with psycopg.connect(sync_url) as connection:
            assert not {"consumed_stream_id", "credential_expires_at"} & _columns(
                connection, "agent_host_run_leases"
            )
            assert "max_concurrent_runs" not in _columns(connection, "workflow_flows")
            assert not _constraint_exists(connection, _RUN_CAP_CHECK)
            assert "version" not in _columns(connection, "agent_runtime_profiles")
            assert not _index_exists(connection, "uq_agent_runtime_profile_org_default")
            assert _index_exists(connection, "ix_users_telegram_username")
            assert _profile_user_fk_action(connection) == "n"

        migrate("upgrade", REVISION)
        with psycopg.connect(sync_url) as connection:
            assert _index_exists(connection, "uq_agent_runtime_profile_org_default")
            assert _profile_user_fk_action(connection) == "c"
            assert _constraint_exists(connection, _RUN_CAP_CHECK)
