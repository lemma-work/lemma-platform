"""Migration 0066 moves the agent's behaviour-driving metadata keys into columns.

Seeded at 0064 with a conversation for every origin the keys could say, runs
carrying every run key, and messages carrying every message key -- plus the
values nothing can read (an unknown ``source``, an id that is not one, a
second copy of a send id) -- then checked after each step against a real
Postgres: the backfill is SQL over JSON, which only the database answers for.

agent_surfaces' keys land in its own ``surface_conversation_state``. Down
again, the columns and that table are gone and the JSON is as it was -- with
what the table learned after the upgrade written back into its keys; up again,
the same typed values.
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

# A chain walk on its own Postgres, like the other migration tests: the nightly
# protected lane.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0065_schedule_reset_and_author"
REVISION = "0066_agent_typed_state"

_CONVERSATION_COLUMNS = (
    "origin",
    "workflow_run_id",
    "schedule_type",
    "parent_run_id",
    "spawn_key",
    "reports_to_parent",
    "delegations_out",
    "ask",
    "call",
    "cwd",
    "workspace_id",
    "workspace_name",
    "repo",
    "todos",
)
_RUN_COLUMNS = (
    "trigger",
    "kind",
    "private_note",
    "asked_in_lemma",
    "written_in",
    "resumed_tool_call_id",
    "retried_run_id",
    "queued_behind_run_id",
    "queued_leftovers_behind",
    "stop_requested_after_sequence",
    "delivered_to_parent_at",
    "suspended_while_parked",
    "schedule_firing",
    "failure",
)
_MESSAGE_COLUMNS = (
    "queue_state",
    "steered_into_run_id",
    "steer_dispatched_at",
    "client_message_id",
    "private_note",
    "private_note_in_dm",
    "written_in",
    "answer",
    "held_for_a_person",
    "folded_from",
    "synthetic_tool_return",
    "parked_in_run_id",
    "run_step_seq",
)


def _migrate(database_url: str, direction: str, revision: str) -> str:
    result = subprocess.run(
        ["uv", "run", "--no-sync", "alembic", direction, revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return result.stdout + result.stderr


class _Ids(dict[str, UUID]):
    def __missing__(self, name: str) -> UUID:
        self[name] = uuid4()
        return self[name]


def _place(ids: _Ids) -> dict[str, object]:
    """Every key a surface binding and agent_surfaces' records were kept under."""
    return {
        "surface_id": str(ids["surface_row"]),
        "surface_platform": "TELEGRAM",
        "conversation_kind": "CHANNEL",
        "external_channel_id": "-100123",
        "external_thread_id": "-100123",
        "external_user_id": "42",
        "external_message_id": "9001",
        "channel_name": "Support",
        "route_key": "channel:-100123",
        "routed_agent_id": str(ids["routed_agent"]),
        "agent_display_name": "Lem",
        "surface_event_metadata": {"platform": "TELEGRAM", "chat_id": "-100123"},
        "surface_held_replies": {
            "asked_at": "2026-10-20T09:30:00+00:00",
            "replies": [],
            "version": "v1",
        },
        "surface_free_text_answer_for": "call-7",
    }


_SURFACE_COLUMNS = (
    "conversation_id",
    "surface_id",
    "surface_platform",
    "conversation_kind",
    "external_channel_id",
    "external_thread_id",
    "external_user_id",
    "external_message_id",
    "channel_name",
    "route_key",
    "routed_agent_id",
    "agent_display_name",
    "surface_event_metadata",
    "web_widget_id",
    "web_session_id",
    "held_replies",
    "free_text_answer_for",
)


def _surfaces(plain_url: str) -> dict[UUID, dict[str, object]]:
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            f"SELECT {', '.join(_SURFACE_COLUMNS)} FROM surface_conversation_state"
        ).fetchall()
    return {
        row[0]: {
            name: value
            for name, value in zip(_SURFACE_COLUMNS[1:], row[1:], strict=True)
            if value is not None
        }
        for row in rows
    }


def _assert_surfaces(rows: dict[UUID, dict[str, object]], ids: _Ids) -> None:
    place = _place(ids)
    assert rows == {
        ids["surface"]: {
            **{
                key: value
                for key, value in place.items()
                if not key.startswith("surface_") or key == "surface_platform"
            },
            "surface_id": ids["surface_row"],
            "routed_agent_id": ids["routed_agent"],
            "surface_event_metadata": place["surface_event_metadata"],
            "held_replies": place["surface_held_replies"],
            "free_text_answer_for": "call-7",
        },
        ids["widget"]: {
            "web_widget_id": ids["widget_row"],
            "web_session_id": ids["widget_session"],
        },
        ids["notification"]: {"surface_platform": "SLACK"},
        # What could be read of it; the rest is counted.
        ids["surface_junk"]: {"surface_platform": "TELEGRAM"},
    }


def _seed(plain_url: str) -> _Ids:
    ids = _Ids()
    ask = {
        "from_pod_id": str(ids["asking_pod"]),
        "from_pod_name": "Sales",
        "from_conversation_id": str(ids["asking_conversation"]),
        "for_user_id": str(ids["owner"]),
        "mode": "AS_PERSON",
        "chain": {"chain_id": str(ids["chain"]), "depth": 0, "pod_ids": []},
    }
    conversations: list[tuple[str, dict[str, object] | None, dict[str, object]]] = [
        (
            "workflow",
            {
                "source": "WORKFLOW_RUN",
                "workflow_run_id": str(ids["workflow_run"]),
                "cwd": "/workspace/repos/acme/web",
                "repo": {"owner": "acme", "repo": "web"},
                "todos": [{"content": "Ship it", "done": False}],
            },
            {},
        ),
        ("asked", {"source": "pod_ask", "ask": ask, "pod_asks_out": True}, {}),
        (
            "coordinator",
            {
                "call": {"call_id": str(ids["coordinator"]), "role": "coordinator"},
                "subagents_out": True,
                "workspace": {"id": "ws-1", "cwd": "/workspace/w"},
                "workspace_name": "main",
            },
            {},
        ),
        (
            "child",
            {
                "is_sub_agent": True,
                "parent_run_id": str(ids["parent_run"]),
                "spawn_key": "task-key",
                "reports_to_parent": True,
                "call": {"call_id": str(ids["coordinator"]), "role": "worker"},
            },
            {"parent": "coordinator"},
        ),
        (
            "child_copy",
            {
                "is_sub_agent": True,
                "parent_run_id": str(ids["parent_run"]),
                "spawn_key": "task-key",
            },
            {"parent": "coordinator", "later": True},
        ),
        (
            "schedule",
            {
                "source": "SCHEDULE",
                "started_by": "SCHEDULE",
                "schedule_type": "DATASTORE",
            },
            {},
        ),
        (
            "notification",
            {"source": "notification", "surface_platform": "SLACK"},
            {},
        ),
        (
            "widget",
            {
                "source": "web_widget",
                "web_widget_id": str(ids["widget_row"]),
                "web_session_id": str(ids["widget_session"]),
            },
            {},
        ),
        ("surface", {"source": "agent_surfaces", **_place(ids)}, {}),
        (
            "surface_junk",
            {
                "surface_id": "not-a-uuid",
                "surface_platform": "TELEGRAM",
                "route_key": 7,
            },
            {},
        ),
        ("channel", {"source": "lemma_channel"}, {}),
        ("project", None, {"type": "PROJECT"}),
        ("chat", {"lemma_attached_file": "/notes.md"}, {}),
        ("mystery", {"source": "mystery"}, {}),
        (
            "bad_workflow",
            {"workflow_run_id": "not-a-uuid", "schedule_type": "MONTHLY"},
            {},
        ),
    ]
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
        for name, metadata, extra in conversations:
            parent = extra.get("parent")
            run(
                "INSERT INTO agent_conversations (id, user_id, pod_id, "
                "conversation_type, parent_id, conversation_metadata, is_archived, "
                "last_activity_at, created_at, updated_at) VALUES (%s, %s, %s, %s, "
                "%s, %s, false, now(), now() + %s * interval '1 second', now())",
                (
                    ids[name],
                    ids["owner"],
                    ids["pod"],
                    extra.get("type", "CHAT"),
                    ids[str(parent)] if parent else None,
                    json.dumps(metadata) if metadata is not None else None,
                    1 if extra.get("later") else 0,
                ),
            )

        runs: list[tuple[str, dict[str, object], str]] = [
            (
                "asked_run",
                {
                    "source": "user_message",
                    "private_note": True,
                    "asked_in_lemma": True,
                    "written_in_lemma": "outsiders",
                },
                "COMPLETED",
            ),
            (
                "followup_run",
                {
                    "source": "queued_messages",
                    "queued_behind_agent_run_id": str(ids["asked_run"]),
                    "queued_leftovers_behind": [str(ids["asked_run"])],
                },
                "COMPLETED",
            ),
            (
                "resume_run",
                {
                    "source": "approval_resume",
                    "resumed_tool_call_id": "call-1",
                    "suspended_while_parked": {
                        "tool_call_id": "call-1",
                        "tool_name": "ask_user",
                    },
                },
                "COMPLETED",
            ),
            ("wake_run", {"source": "wait_resume"}, "COMPLETED"),
            (
                "retry_run",
                {
                    "source": "manual_retry",
                    "retried_agent_run_id": str(ids["asked_run"]),
                    "failure": {"code": "MODEL_ERROR", "reason": "overloaded"},
                    "stop_requested_after_sequence": 7,
                },
                "FAILED",
            ),
            (
                "schedule_run",
                {
                    "source": "SCHEDULE",
                    "schedule_name": "daily",
                    "schedule_type": "TIME",
                },
                "COMPLETED",
            ),
            (
                "child_run",
                {"source": "surprise", "collected_by_parent": "delivery"},
                "COMPLETED",
            ),
        ]
        # Where each run is, when not the plain chat: its kind is read off it.
        placed = {
            "fired_run": "schedule",
            "person_in_schedule_run": "schedule",
            "voice_run": "coordinator",
            "widget_run": "widget",
            "worker_run": "child",
        }
        runs += [
            ("fired_run", {"source": "SCHEDULE"}, "COMPLETED"),
            ("person_in_schedule_run", {"source": "user_message"}, "COMPLETED"),
            ("voice_run", {"source": "user_message"}, "COMPLETED"),
            ("widget_run", {"source": "user_message"}, "COMPLETED"),
            ("worker_run", {"source": "user_message"}, "COMPLETED"),
        ]
        for name, metadata, status in runs:
            run(
                "INSERT INTO agent_runs (id, conversation_id, status, agent_runtime, "
                "started_at, finished_at, run_metadata, created_at, updated_at) "
                "VALUES (%s, %s, %s, '{}', now(), "
                "'2026-10-01T12:00:00+00:00', %s, now(), now())",
                (
                    ids[name],
                    ids[placed.get(name, "chat")],
                    status,
                    json.dumps(metadata),
                ),
            )

        messages: list[tuple[str, str, str, dict[str, object], str | None]] = [
            ("queued", "user", "TEXT", {"during_active_run": True}, None),
            (
                "steered",
                "user",
                "TEXT",
                {
                    "during_active_run": True,
                    "steered_into_run": str(ids["followup_run"]),
                },
                None,
            ),
            (
                "undelivered",
                "user",
                "TEXT",
                {
                    "during_active_run": True,
                    "steer_dispatched_at": "2026-10-01T12:00:05+00:00",
                    "steer_undelivered": "turn_ended",
                },
                None,
            ),
            (
                "waiting",
                "user",
                "TEXT",
                {
                    "during_active_run": True,
                    "waiting_on_answer": str(ids["asked_run"]),
                },
                None,
            ),
            (
                "note",
                "user",
                "TEXT",
                {
                    "client_message_id": "send-1",
                    "private_note": True,
                    "private_note_in_dm": True,
                    "written_in_lemma": "group",
                },
                None,
            ),
            ("note_copy", "user", "TEXT", {"client_message_id": "send-1"}, None),
            ("final", "assistant", "TEXT", {"is_final_answer": True}, None),
            (
                "last_words",
                "assistant",
                "TEXT",
                {"is_final_answer": True, "answer_from_last_words": True},
                None,
            ),
            (
                "narration",
                "assistant",
                "TEXT",
                {
                    "is_final_answer": False,
                    "is_intermediate_assistant_message": True,
                },
                None,
            ),
            ("thought", "assistant", "THINKING", {"is_final_answer": False}, None),
            (
                "held",
                "user",
                "TEXT",
                {"held_for_a_person": True, "folded_from": str(ids["elsewhere"])},
                None,
            ),
            (
                "made_up",
                "tool",
                "TOOL_RETURN",
                {"synthetic_tool_return": True},
                None,
            ),
            (
                "parked",
                "assistant",
                "TOOL_CALL",
                {"parked_in_run": True},
                "asked_run",
            ),
            (
                "garbled",
                "user",
                "TEXT",
                {"during_active_run": True, "steered_into_run": "garbage"},
                None,
            ),
        ]
        for sequence, (name, role, kind, metadata, run_name) in enumerate(messages):
            run(
                "INSERT INTO agent_messages (id, conversation_id, agent_run_id, "
                "sequence, role, kind, message_metadata, created_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, now())",
                (
                    ids[name],
                    ids["chat"],
                    ids[run_name] if run_name else None,
                    sequence,
                    role,
                    kind,
                    json.dumps(metadata),
                ),
            )
    return ids


def _rows(plain_url: str, table: str, columns: tuple[str, ...]) -> dict[UUID, dict]:
    with psycopg.connect(plain_url) as connection:
        cursor = connection.execute(f"SELECT id, {', '.join(columns)} FROM {table}")
        return {
            row[0]: dict(zip(columns, row[1:], strict=True))
            for row in cursor.fetchall()
        }


def _metadata(plain_url: str) -> dict[str, dict[UUID, object]]:
    with psycopg.connect(plain_url) as connection:
        return {
            table: dict(
                connection.execute(f"SELECT id, {column} FROM {table}").fetchall()
            )
            for table, column in (
                ("agent_conversations", "conversation_metadata"),
                ("agent_runs", "run_metadata"),
                ("agent_messages", "message_metadata"),
            )
        }


def _columns(plain_url: str, table: str) -> set[str]:
    with psycopg.connect(plain_url) as connection:
        return {
            name
            for (name,) in connection.execute(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name = %s",
                (table,),
            ).fetchall()
        }


def _typed(plain_url: str) -> tuple[dict, dict, dict]:
    return (
        _rows(plain_url, "agent_conversations", _CONVERSATION_COLUMNS),
        _rows(plain_url, "agent_runs", _RUN_COLUMNS),
        _rows(plain_url, "agent_messages", _MESSAGE_COLUMNS),
    )


def _assert_conversations(rows: dict[UUID, dict], ids: _Ids) -> None:
    origins = {
        name: rows[ids[name]]["origin"]
        for name in (
            "workflow",
            "asked",
            "coordinator",
            "child",
            "schedule",
            "notification",
            "widget",
            "surface",
            "channel",
            "project",
            "chat",
            "mystery",
            "bad_workflow",
        )
    }
    assert origins == {
        "workflow": "WORKFLOW_STEP",
        "asked": "POD_ASK",
        "coordinator": "CALL",
        "child": "SUBAGENT",
        "schedule": "SCHEDULE",
        "notification": "NOTIFICATION",
        "widget": "CONTACT",
        "surface": "SURFACE",
        "channel": "CHANNEL",
        "project": "PROJECT",
        "chat": "CHAT",
        # Unmapped: the default, counted.
        "mystery": "CHAT",
        # An id that is not one says nothing about a workflow.
        "bad_workflow": "CHAT",
    }
    assert rows[ids["schedule"]]["schedule_type"] == "DATASTORE"
    assert rows[ids["bad_workflow"]]["schedule_type"] is None
    workflow = rows[ids["workflow"]]
    assert workflow["workflow_run_id"] == ids["workflow_run"]
    assert workflow["cwd"] == "/workspace/repos/acme/web"
    assert workflow["repo"] == {"owner": "acme", "repo": "web"}
    assert workflow["todos"] == [{"content": "Ship it", "done": False}]
    assert rows[ids["bad_workflow"]]["workflow_run_id"] is None

    asked = rows[ids["asked"]]
    assert asked["ask"]["from_conversation_id"] == str(ids["asking_conversation"])
    assert asked["delegations_out"] is True

    coordinator = rows[ids["coordinator"]]
    assert coordinator["call"] == {
        "call_id": str(ids["coordinator"]),
        "role": "coordinator",
    }
    assert coordinator["delegations_out"] is True
    assert coordinator["cwd"] == "/workspace/w"
    assert coordinator["workspace_id"] == "ws-1"
    assert coordinator["workspace_name"] == "main"

    child = rows[ids["child"]]
    assert child["parent_run_id"] == ids["parent_run"]
    assert child["spawn_key"] == "task-key"
    assert child["reports_to_parent"] is True
    assert child["call"]["role"] == "worker"
    # The same task twice under one parent: both keep it.
    assert rows[ids["child_copy"]]["spawn_key"] == "task-key"

    chat = rows[ids["chat"]]
    assert chat["todos"] == []
    assert chat["delegations_out"] is False


def _assert_runs(rows: dict[UUID, dict], ids: _Ids) -> None:
    triggers = {
        name: rows[ids[name]]["trigger"]
        for name in (
            "asked_run",
            "followup_run",
            "resume_run",
            "wake_run",
            "retry_run",
            "schedule_run",
            "child_run",
        )
    }
    assert triggers == {
        "asked_run": "MESSAGE",
        "followup_run": "FOLLOW_UP",
        "resume_run": "RESUME",
        "wake_run": "WAKE",
        "retry_run": "RETRY",
        "schedule_run": "SCHEDULE_FIRE",
        "child_run": "MESSAGE",
    }
    asked = rows[ids["asked_run"]]
    assert (asked["private_note"], asked["asked_in_lemma"], asked["written_in"]) == (
        True,
        True,
        "OUTSIDERS",
    )
    followup = rows[ids["followup_run"]]
    assert followup["queued_behind_run_id"] == ids["asked_run"]
    assert followup["queued_leftovers_behind"] == [str(ids["asked_run"])]
    resume = rows[ids["resume_run"]]
    assert resume["resumed_tool_call_id"] == "call-1"
    assert resume["suspended_while_parked"] == {
        "tool_call_id": "call-1",
        "tool_name": "ask_user",
    }
    retry = rows[ids["retry_run"]]
    assert retry["retried_run_id"] == ids["asked_run"]
    assert retry["failure"] == {"code": "MODEL_ERROR", "reason": "overloaded"}
    assert retry["stop_requested_after_sequence"] == 7
    assert rows[ids["schedule_run"]]["schedule_firing"] == {
        "schedule_name": "daily",
        "trigger_type": "TIME",
    }
    delivered = rows[ids["child_run"]]["delivered_to_parent_at"]
    assert delivered is not None and delivered.isoformat().startswith("2026-10-01T12")
    assert rows[ids["wake_run"]]["delivered_to_parent_at"] is None
    kinds = {
        name: rows[ids[name]]["kind"]
        for name in (
            "asked_run",
            "fired_run",
            "person_in_schedule_run",
            "voice_run",
            "widget_run",
            "worker_run",
        )
    }
    assert kinds == {
        "asked_run": "CHAT",
        "fired_run": "SCHEDULE",
        # A person writing into a schedule's conversation: their turn.
        "person_in_schedule_run": "CHAT",
        "voice_run": "CALL",
        "widget_run": "CONTACT",
        "worker_run": "SUBAGENT",
    }


def _assert_messages(rows: dict[UUID, dict], ids: _Ids) -> None:
    states = {
        name: rows[ids[name]]["queue_state"]
        for name in ("queued", "steered", "undelivered", "waiting", "note", "garbled")
    }
    assert states == {
        "queued": "QUEUED",
        "steered": "STEERED",
        "undelivered": "STEER_UNDELIVERED",
        "waiting": "WAITING_ON_ANSWER",
        "note": "NONE",
        "garbled": "STEERED",
    }
    assert rows[ids["steered"]]["steered_into_run_id"] == ids["followup_run"]
    assert rows[ids["garbled"]]["steered_into_run_id"] is None
    assert rows[ids["undelivered"]]["steer_dispatched_at"] is not None

    note = rows[ids["note"]]
    assert note["client_message_id"] == "send-1"
    assert (note["private_note"], note["private_note_in_dm"], note["written_in"]) == (
        True,
        True,
        "GROUP",
    )
    # The later message under the same send id gives it up to the unique index.
    assert rows[ids["note_copy"]]["client_message_id"] is None

    answers = {
        name: rows[ids[name]]["answer"]
        for name in ("final", "last_words", "narration", "thought", "queued")
    }
    assert answers == {
        "final": "FINAL",
        "last_words": "LAST_WORDS",
        "narration": "INTERMEDIATE",
        "thought": "NOT_FINAL",
        "queued": None,
    }
    held = rows[ids["held"]]
    assert (held["held_for_a_person"], held["folded_from"]) == (True, ids["elsewhere"])
    assert rows[ids["made_up"]]["synthetic_tool_return"] is True
    assert rows[ids["parked"]]["parked_in_run_id"] == ids["asked_run"]


def _assert_run_log_keeps_bytes(plain_url: str) -> None:
    """The run log is new and empty, and hands back the bytes it was given:
    TEXT, so neither key order nor spacing moves."""
    assert _columns(plain_url, "agent_run_steps") == {
        "id",
        "created_at",
        "conversation_id",
        "run_id",
        "seq",
        "kind",
        "messages",
    }
    written = '[{"parts": [{"tool_name":"x","args":"{\\"b\\": 1,  \\"a\\": 2}"}]}]'
    with psycopg.connect(plain_url) as connection:
        conversation_id = connection.execute(
            "SELECT id FROM agent_conversations LIMIT 1"
        ).fetchone()[0]
        connection.execute(
            "INSERT INTO agent_run_steps "
            "(id, created_at, conversation_id, seq, kind, messages) "
            "VALUES (%s, now(), %s, 1, 'STEP', %s)",
            (uuid4(), conversation_id, written),
        )
        (read,) = connection.execute(
            "SELECT messages FROM agent_run_steps WHERE conversation_id = %s",
            (conversation_id,),
        ).fetchone()
        assert read == written
        assert connection.execute(
            "SELECT count(*) FROM agent_messages WHERE run_step_seq IS NOT NULL"
        ).fetchone() == (0,)


def test_the_metadata_keys_land_in_typed_columns_and_come_back_out() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)
        metadata_before = _metadata(plain_url)

        log = _migrate(database_url, "upgrade", REVISION)
        conversations, runs, messages = _typed(plain_url)
        _assert_conversations(conversations, ids)
        _assert_runs(runs, ids)
        _assert_messages(messages, ids)
        surfaces = _surfaces(plain_url)
        _assert_surfaces(surfaces, ids)
        assert "backfilled 4 surface_conversation_state rows" in log
        assert "1 agent_conversations rows hold a 'surface_id'" in log
        assert "1 agent_conversations rows hold a 'route_key'" in log
        assert "1 agent_conversations rows hold a 'schedule_type'" in log
        # What could not be read is counted, per table and key.
        assert "1 agent_conversations rows hold a 'source'" in log
        assert "1 agent_conversations rows hold a 'workflow_run_id'" in log
        assert "1 agent_runs rows hold a 'source'" in log
        assert "1 agent_messages rows hold a 'steered_into_run'" in log
        assert "1 later client_message_id copies dropped" in log
        # Expand only: the JSON is untouched.
        assert _metadata(plain_url) == metadata_before
        _assert_run_log_keeps_bytes(plain_url)

        # Learned after the upgrade: only agent_surfaces' table hears of it.
        with psycopg.connect(plain_url) as connection:
            connection.execute(
                "UPDATE surface_conversation_state SET external_thread_id = '777', "
                "free_text_answer_for = NULL WHERE conversation_id = %s",
                (ids["surface"],),
            )
        _migrate(database_url, "downgrade", BEFORE)
        assert not set(_CONVERSATION_COLUMNS) & _columns(
            plain_url, "agent_conversations"
        )
        assert not set(_RUN_COLUMNS) & _columns(plain_url, "agent_runs")
        assert not set(_MESSAGE_COLUMNS) & _columns(plain_url, "agent_messages")
        assert not _columns(plain_url, "surface_conversation_state")
        assert not _columns(plain_url, "agent_run_steps")
        restored = _metadata(plain_url)
        surfaced = restored["agent_conversations"][ids["surface"]]
        assert surfaced["external_thread_id"] == "777"
        # A record cleared after the upgrade is not brought back by the
        # downgrade; the key the old code reads still holds what it wrote.
        assert surfaced["surface_free_text_answer_for"] == "call-7"
        surfaced["external_thread_id"] = "-100123"
        assert restored == metadata_before

        _migrate(database_url, "upgrade", REVISION)
        assert _typed(plain_url) == (conversations, runs, messages)
        again = _surfaces(plain_url)
        assert again[ids["surface"]]["external_thread_id"] == "777"
        again[ids["surface"]]["external_thread_id"] = "-100123"
        assert again == surfaces
