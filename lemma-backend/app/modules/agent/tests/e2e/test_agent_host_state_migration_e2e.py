"""Migration 0066 carries the Agent Host's metadata keys into its own tables.

Seeded at 0064 with a conversation bound to a provider session (every field
the binding can hold), one whose binding is not an object, and runs carrying
``execution``, ``host_native_commands`` and ``final_answer`` -- and one whose
``execution`` is a string nothing can read. Up, the two tables hold what the
keys held; down, what the tables learned after the upgrade is back in the keys;
up again, the same rows.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from uuid import UUID, uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url
from app.modules.agent.tests.e2e.test_agent_pauses_migration_e2e import (
    BEFORE,
    REVISION,
    _migrate,
)

# A chain walk on its own Postgres, like the other migration tests: the nightly
# protected lane.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

_RUN_CREATED = datetime(2026, 10, 20, 9, 30, tzinfo=timezone.utc)
_EXECUTION = {"target": "host", "host_id": str(uuid4()), "root": "/Users/o/x"}
_NATIVE = {"enabled": True, "host_id": _EXECUTION["host_id"]}
_ANSWER = {"answer": "Booked.", "status": "done"}


def _binding(ids: dict[str, UUID]) -> dict[str, object]:
    return {
        "session_id": "codex-rollout-1",
        "harness_id": str(ids["harness"]),
        "host_id": str(ids["host"]),
        "run_id": str(ids["bound_run"]),
        "run_created_at": _RUN_CREATED.isoformat(),
        "host_cwd": "/Users/owner/code/app",
        "instructions_digest": "a" * 64,
        "pending_instructions": {"run_id": str(ids["next_run"]), "digest": "b" * 64},
        "pending_prompt": {"run_id": str(ids["next_run"]), "seen_through": 12},
        "prompted_run_id": str(ids["bound_run"]),
        "prompted_through": 9,
    }


def _seed(plain_url: str) -> dict[str, UUID]:
    ids = {
        name: uuid4()
        for name in (
            "org",
            "owner",
            "pod",
            "bound",
            "junk",
            "harness",
            "host",
            "bound_run",
            "next_run",
            "plain_run",
            "junk_run",
        )
    }
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        run(
            "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
            "updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', now(), now())",
            (ids["org"], f"org-{ids['org'].hex[:8]}"),
        )
        run(
            "INSERT INTO users (id, email, is_verified, is_active, is_superuser, "
            "is_deleted, created_at, updated_at) VALUES "
            "(%s, %s, true, true, false, false, now(), now())",
            (ids["owner"], f"{ids['owner'].hex[:8]}@example.com"),
        )
        run(
            "INSERT INTO pods (id, user_id, organization_id, name, config, "
            "is_deleted, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'Pod', '{}', false, now(), now())",
            (ids["pod"], ids["owner"], ids["org"]),
        )
        for conversation, binding in (
            ("bound", _binding(ids)),
            ("junk", "not-a-binding"),
        ):
            run(
                "INSERT INTO agent_conversations (id, user_id, pod_id, "
                "conversation_type, is_archived, conversation_metadata, "
                "last_activity_at, created_at, updated_at) VALUES "
                "(%s, %s, %s, 'CHAT', false, %s, now(), now(), now())",
                (
                    ids[conversation],
                    ids["owner"],
                    ids["pod"],
                    json.dumps({"agent_host_provider_session_id": binding}),
                ),
            )
        for name, metadata in (
            (
                "bound_run",
                {
                    "execution": _EXECUTION,
                    "host_native_commands": _NATIVE,
                    "final_answer": _ANSWER,
                },
            ),
            ("next_run", {"execution": {"target": "vm"}}),
            ("plain_run", {"source": "user_message"}),
            ("junk_run", {"execution": "host"}),
        ):
            run(
                "INSERT INTO agent_runs (id, conversation_id, status, agent_runtime, "
                "run_metadata, started_at, finished_at, created_at, updated_at) "
                "VALUES (%s, %s, 'COMPLETED', '{}', %s, now(), now(), now(), now())",
                (ids[name], ids["bound"], json.dumps(metadata)),
            )
    return ids


_SESSION_COLUMNS = (
    "session_id",
    "harness_id",
    "host_id",
    "run_id",
    "run_created_at",
    "host_cwd",
    "instructions_digest",
    "pending_instructions_run_id",
    "pending_instructions_digest",
    "pending_prompt_run_id",
    "pending_prompt_seen_through",
    "prompted_run_id",
    "prompted_through",
)


def _sessions(plain_url: str) -> dict[UUID, dict[str, object]]:
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            f"SELECT conversation_id, {', '.join(_SESSION_COLUMNS)} "
            "FROM agent_host_sessions"
        ).fetchall()
    return {row[0]: dict(zip(_SESSION_COLUMNS, row[1:], strict=True)) for row in rows}


def _records(plain_url: str) -> dict[UUID, tuple[object, object, object]]:
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            "SELECT run_id, execution, host_native_commands, final_answer "
            "FROM agent_host_run_records"
        ).fetchall()
    return {row[0]: tuple(row[1:]) for row in rows}


def _metadata_key(plain_url: str, table: str, column: str, row_id: UUID, key: str):
    with psycopg.connect(plain_url) as connection:
        (value,) = connection.execute(
            f"SELECT {column}->%s FROM {table} WHERE id = %s", (key, row_id)
        ).fetchone()
    return value


def _assert_backfilled(plain_url: str, ids: dict[str, UUID]) -> None:
    assert _sessions(plain_url) == {
        ids["bound"]: {
            "session_id": "codex-rollout-1",
            "harness_id": ids["harness"],
            "host_id": ids["host"],
            "run_id": ids["bound_run"],
            "run_created_at": _RUN_CREATED,
            "host_cwd": "/Users/owner/code/app",
            "instructions_digest": "a" * 64,
            "pending_instructions_run_id": ids["next_run"],
            "pending_instructions_digest": "b" * 64,
            "pending_prompt_run_id": ids["next_run"],
            "pending_prompt_seen_through": 12,
            "prompted_run_id": ids["bound_run"],
            "prompted_through": 9,
        }
    }, "a binding that is not an object was carried"
    assert _records(plain_url) == {
        ids["bound_run"]: (_EXECUTION, _NATIVE, _ANSWER),
        ids["next_run"]: ({"target": "vm"}, None, None),
    }, "a run with no record, or an unreadable one, was carried"


def test_the_agent_host_keys_land_in_its_tables_and_go_back_out() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)

        log = _migrate(database_url, "upgrade", REVISION)
        _assert_backfilled(plain_url, ids)
        assert "backfilled 1 agent_host_sessions and 2 agent_host_run_records" in log
        assert (
            "1 agent_conversations rows hold a 'agent_host_provider_session_id'" in log
        )
        assert "1 agent_runs rows hold a 'execution'" in log

        # Learned after the upgrade: only the tables hear of it.
        with psycopg.connect(plain_url) as connection:
            connection.execute(
                "UPDATE agent_host_sessions SET host_cwd = '/Users/owner/elsewhere', "
                "pending_prompt_run_id = NULL, pending_prompt_seen_through = NULL "
                "WHERE conversation_id = %s",
                (ids["bound"],),
            )
            connection.execute(
                "INSERT INTO agent_host_run_records (run_id, final_answer, "
                "created_at, updated_at) VALUES (%s, %s, now(), now())",
                (ids["plain_run"], json.dumps(_ANSWER)),
            )

        _migrate(database_url, "downgrade", BEFORE)
        with psycopg.connect(plain_url) as connection:
            (gone,) = connection.execute(
                "SELECT to_regclass('agent_host_sessions') IS NULL "
                "AND to_regclass('agent_host_run_records') IS NULL"
            ).fetchone()
        assert gone
        restored = _metadata_key(
            plain_url,
            "agent_conversations",
            "conversation_metadata",
            ids["bound"],
            "agent_host_provider_session_id",
        )
        expected = _binding(ids)
        expected["host_cwd"] = "/Users/owner/elsewhere"
        del expected["pending_prompt"]
        assert {k: v for k, v in restored.items() if k != "run_created_at"} == {
            k: v for k, v in expected.items() if k != "run_created_at"
        }
        assert datetime.fromisoformat(restored["run_created_at"]) == _RUN_CREATED
        assert (
            _metadata_key(
                plain_url,
                "agent_runs",
                "run_metadata",
                ids["plain_run"],
                "final_answer",
            )
            == _ANSWER
        )

        _migrate(database_url, "upgrade", REVISION)
        again = _sessions(plain_url)[ids["bound"]]
        assert again["host_cwd"] == "/Users/owner/elsewhere"
        assert again["pending_prompt_run_id"] is None
        assert again["prompted_through"] == 9
        assert _records(plain_url)[ids["plain_run"]] == (None, None, _ANSWER)
