"""Migration 0066 carries every pause into ``agent_pauses``.

Seeded at 0064 with one of each: an open question, approval, budget pause,
Agent Host permission and sign-in; decisions that resolved, superseded,
expired and claimed an approval; a call closed by its return; an armed wait and
a woken one -- and what cannot be carried: an open ``wait_for`` with no wait, a
decision whose call is gone, a wait in a status nobody knows. Checked against a
real Postgres, because the backfill is SQL over JSON. Down again, what the new
table learned is copied back to the old ones; up again, the same pauses.
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


_BUDGET = {"dimension": "MODEL_REQUESTS", "spent": 200}
_PERMISSION = {
    "request_id": "native-shell",
    "input": {"cmd": "rm -rf build"},
    "options": [{"option_id": "once", "kind": "allowonce", "name": "Allow"}],
}

#: name -> (tool, tool_args, has_return)
_CALLS: dict[str, tuple[str, dict[str, object], bool]] = {
    "q_open": ("ask_user", {"questions": [{"question": "Which?"}]}, False),
    "a_open": ("request_approval", {"tool_name": "exec_command"}, False),
    "b_open": (
        "request_approval",
        {"tool_name": "continue_running", "run_budget_pause": _BUDGET},
        False,
    ),
    "h_open": (
        "request_approval",
        {"tool_name": "exec_command", "agent_host_permission": _PERMISSION},
        False,
    ),
    "s_open": ("browser_sign_in", {"origin": "https://example.com"}, False),
    "q_decided": ("ask_user", {}, True),
    "a_superseded": ("request_approval", {"tool_name": "exec_command"}, True),
    "q_expired": ("ask_user", {}, True),
    "a_claimed": ("request_approval", {"tool_name": "exec_command"}, False),
    "q_closed": ("ask_user", {}, True),
    "w_active": ("wait_for", {"seconds": 600}, False),
    "w_done": ("wait_for", {"seconds": 600}, True),
    "w_orphan": ("wait_for", {"seconds": 600}, False),
}

#: name -> (decision, response, claimed)
_DECISIONS: dict[str, tuple[str, dict[str, object], bool]] = {
    "q_decided": ("APPROVE_ONCE", {"answers": {"Which?": "this"}}, False),
    "a_superseded": ("DENY", {"superseded_by_new_message": True}, False),
    "q_expired": ("DENY", {"expired_unanswered": True}, False),
    "a_claimed": ("APPROVE_ONCE", {}, True),
    # A new message used to deny a woken wait's call too: the wait covers it.
    "w_done": ("DENY", {"superseded_by_new_message": True}, False),
    # Its call is gone.
    "ghost": ("APPROVE_ONCE", {}, False),
}


def _seed(plain_url: str) -> _Ids:
    ids = _Ids()
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
        for conversation in ("chat", "other"):
            run(
                "INSERT INTO agent_conversations (id, user_id, pod_id, "
                "conversation_type, is_archived, last_activity_at, created_at, "
                "updated_at) VALUES (%s, %s, %s, 'CHAT', false, now(), now(), now())",
                (ids[conversation], ids["owner"], ids["pod"]),
            )
            run(
                "INSERT INTO agent_runs (id, conversation_id, status, agent_runtime, "
                "started_at, finished_at, created_at, updated_at) "
                "VALUES (%s, %s, 'COMPLETED', '{}', now(), now(), now(), now())",
                (ids[f"{conversation}_run"], ids[conversation]),
            )
        sequence = 0
        for name, (tool, args, returned) in _CALLS.items():
            for kind, body in (("TOOL_CALL", args), ("TOOL_RETURN", None)):
                if kind == "TOOL_RETURN" and not returned:
                    continue
                run(
                    "INSERT INTO agent_messages (id, conversation_id, agent_run_id, "
                    "sequence, role, kind, tool_name, tool_call_id, tool_args, "
                    "created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, now())",
                    (
                        uuid4(),
                        ids["chat"],
                        ids["chat_run"],
                        sequence,
                        "assistant" if kind == "TOOL_CALL" else "tool",
                        kind,
                        tool,
                        name,
                        json.dumps(body) if body is not None else None,
                    ),
                )
                sequence += 1
        for name, (decision, response, claimed) in _DECISIONS.items():
            run(
                "INSERT INTO agent_approval_decisions (id, conversation_id, "
                "agent_run_id, approval_id, tool_name, decision, response, "
                "resolved_by_user_id, execution_claimed_at, created_at) VALUES "
                "(%s, %s, %s, %s, 'x', %s, %s, %s, "
                "CASE WHEN %s THEN now() END, now())",
                (
                    ids[f"decision_{name}"],
                    ids["chat"],
                    ids["chat_run"],
                    name,
                    decision,
                    json.dumps(response),
                    ids["owner"],
                    claimed,
                ),
            )
        for name, conversation, status, spec in (
            ("w_active", "chat", "ACTIVE", {"note_to_self": "check the build"}),
            ("w_done", "chat", "COMPLETED", {"woke_because": "TIMER"}),
            ("w_odd", "other", "SNOOZED", {}),
        ):
            run(
                "INSERT INTO agent_conversation_waits (id, conversation_id, "
                "agent_run_id, pod_id, tool_call_id, wait_type, status, "
                "external_ref, scheduled_at, spec, created_at, updated_at) VALUES "
                "(%s, %s, %s, %s, %s, 'TIME', %s, %s, now() + interval '1 hour', "
                "%s, now(), now())",
                (
                    ids[f"wait_{name}"],
                    ids[conversation],
                    ids[f"{conversation}_run"],
                    ids["pod"],
                    name,
                    status,
                    str(uuid4()),
                    json.dumps(spec),
                ),
            )
    return ids


def _pauses(plain_url: str) -> dict[str, dict[str, object]]:
    columns = (
        "id",
        "kind",
        "status",
        "tool_name",
        "resolution",
        "host_permission",
        "budget",
        "wait_type",
        "wait_spec",
        "execution_claimed_at",
        "deadline",
    )
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            f"SELECT tool_call_id, {', '.join(columns)}, "
            "deadline - created_at FROM agent_pauses"
        ).fetchall()
    return {
        row[0]: {**dict(zip(columns, row[1:-1], strict=True)), "lasts": row[-1]}
        for row in rows
    }


def _decision_rows(plain_url: str) -> dict[str, str]:
    with psycopg.connect(plain_url) as connection:
        return dict(
            connection.execute(
                "SELECT approval_id, decision FROM agent_approval_decisions"
            ).fetchall()
        )


def _assert_backfilled(pauses: dict[str, dict[str, object]], ids: _Ids) -> None:
    kinds = {name: pause["kind"] for name, pause in pauses.items()}
    assert kinds == {
        "q_open": "QUESTION",
        "a_open": "APPROVAL",
        "b_open": "BUDGET",
        "h_open": "HOST_PERMISSION",
        "s_open": "SIGN_IN",
        "q_decided": "QUESTION",
        "a_superseded": "APPROVAL",
        "q_expired": "QUESTION",
        "a_claimed": "APPROVAL",
        "w_active": "WAIT",
        "w_done": "WAIT",
    }, "a closed call, an orphaned wait call or a ghost decision was carried"
    statuses = {name: pause["status"] for name, pause in pauses.items()}
    assert statuses == {
        "q_open": "PENDING",
        "a_open": "PENDING",
        "b_open": "PENDING",
        "h_open": "PENDING",
        "s_open": "PENDING",
        "q_decided": "RESOLVED",
        "a_superseded": "SUPERSEDED",
        "q_expired": "EXPIRED",
        "a_claimed": "RESOLVED",
        "w_active": "PENDING",
        "w_done": "RESOLVED",
    }
    assert pauses["b_open"]["budget"] == _BUDGET
    assert pauses["h_open"]["host_permission"] == {
        **_PERMISSION,
        "tool_name": "exec_command",
    }
    assert pauses["q_decided"]["resolution"] == {
        "decision": "APPROVE_ONCE",
        "response": {"answers": {"Which?": "this"}},
        "resolved_by_user_id": str(ids["owner"]),
    }
    assert pauses["a_claimed"]["execution_claimed_at"] is not None
    # Expiry as the code does it: a question for 72 hours, an approval 7 days.
    assert str(pauses["q_open"]["lasts"]) == "3 days, 0:00:00"
    assert str(pauses["a_open"]["lasts"]) == "7 days, 0:00:00"
    assert pauses["s_open"]["deadline"] is None
    # A wait keeps its id, so a queued `wait:<id>` timer still finds it.
    assert pauses["w_active"]["id"] == ids["wait_w_active"]
    assert pauses["w_active"]["wait_type"] == "TIME"
    assert pauses["w_active"]["wait_spec"] == {"note_to_self": "check the build"}
    assert pauses["w_done"]["resolution"] == {"woke_because": "TIMER"}


def test_every_pause_lands_in_agent_pauses_and_goes_back_out() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)

        log = _migrate(database_url, "upgrade", REVISION)
        pauses = _pauses(plain_url)
        _assert_backfilled(pauses, ids)
        assert "agent_pauses backfilled from 2 waits, 4 approval decisions and 5" in log
        assert "1 waits could not be carried into agent_pauses" in log
        assert "1 approval decisions could not be carried" in log
        assert "1 open pausing calls could not be carried" in log

        # Answered after the upgrade: only the new table hears of it.
        with psycopg.connect(plain_url) as connection:
            connection.execute(
                "UPDATE agent_pauses SET status = 'RESOLVED', resolved_at = now(), "
                "resolution = jsonb_build_object('decision', 'DENY', 'response', "
                "'{}'::jsonb, 'resolved_by_user_id', %s::text) "
                "WHERE tool_call_id = 'a_open'",
                (str(ids["owner"]),),
            )

        _migrate(database_url, "downgrade", BEFORE)
        with psycopg.connect(plain_url) as connection:
            (gone,) = connection.execute(
                "SELECT to_regclass('agent_pauses') IS NULL"
            ).fetchone()
        assert gone
        assert _decision_rows(plain_url)["a_open"] == "DENY"

        _migrate(database_url, "upgrade", REVISION)
        again = _pauses(plain_url)
        assert again["a_open"]["status"] == "RESOLVED"
        assert {name: pause["kind"] for name, pause in again.items()} == {
            name: pause["kind"] for name, pause in pauses.items()
        }
        assert {
            name: pause["status"] for name, pause in again.items() if name != "a_open"
        } == {
            name: pause["status"] for name, pause in pauses.items() if name != "a_open"
        }
