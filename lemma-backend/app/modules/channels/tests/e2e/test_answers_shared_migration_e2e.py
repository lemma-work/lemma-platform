"""Migration 0061 gives every channel ``answers_shared``, true.

A group already there when it runs takes the default: from then on the
answers an agent gives a member there with their own access are read by
everybody in it. The downgrade drops the column, and upgrading again works.

It also moves the read number the old code gave somebody named where they have
no seat -- zero, which counted nothing then and would now count the channel's
whole history as unread -- to just before the line naming them.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import UUID, uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

pytestmark = pytest.mark.e2e

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0060_slack_apps"
REVISION = "0061_answers_shared"


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


def _user(run, user_id) -> None:
    run(
        "INSERT INTO users (id, email, is_verified, is_active, is_superuser, "
        "is_deleted, created_at, updated_at) "
        "VALUES (%s, %s, true, true, false, false, now(), now())",
        (user_id, f"{user_id.hex[:8]}@example.com"),
    )


def _a_group(plain_url: str) -> dict[str, UUID]:
    """A pod with one group, written before 0061: six lines, the second a reply
    in the first's thread, and four people with read numbers there.

    - ``owner`` has a seat, at zero, and something unread naming them;
    - ``named`` has no seat, at zero -- as ``_give_a_place`` left them --
      named unread on lines 4 and 6;
    - ``read`` has no seat, at zero, named on line 3 and has read it;
    - ``moved`` has no seat and read to line 2, named unread on line 5.
    """
    org, owner, pod, channel = uuid4(), uuid4(), uuid4(), uuid4()
    people = {name: uuid4() for name in ("named", "read", "moved")}
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        run(
            "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
            "updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', now(), now())",
            (org, f"org-{org.hex[:8]}"),
        )
        for user_id in (owner, *people.values()):
            _user(run, user_id)
        run(
            "INSERT INTO pods (id, user_id, organization_id, name, config, "
            "is_deleted, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'Pod', '{}', false, now(), now())",
            (pod, owner, org),
        )
        run(
            "INSERT INTO channels (id, pod_id, kind, visibility, title, "
            "last_seq, replies, created_at, updated_at) "
            "VALUES (%s, %s, 'GROUP', 'PUBLIC', 'launch', 6, 1, now(), now())",
            (channel, pod),
        )
        lines = {seq: uuid4() for seq in range(1, 7)}
        for seq, line in lines.items():
            run(
                "INSERT INTO channel_messages (id, channel_id, pod_id, seq, "
                "sender_kind, sender_user_id, body, thread_root_id, created_at, "
                "updated_at) "
                "VALUES (%s, %s, %s, %s, 'MEMBER', %s, 'hi', %s, now(), now())",
                (line, channel, pod, seq, owner, lines[1] if seq == 2 else None),
            )
        run(
            "INSERT INTO channel_members (id, channel_id, pod_id, user_id, "
            "seated_by, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, 'CREATOR', now(), now())",
            (uuid4(), channel, pod, owner),
        )
        mentions = [
            (owner, 5, False),
            (people["named"], 4, False),
            (people["named"], 6, False),
            (people["read"], 3, True),
            (people["moved"], 5, False),
        ]
        for user_id, seq, was_read in mentions:
            run(
                "INSERT INTO channel_mentions (id, message_id, channel_id, pod_id, "
                "seq, user_id, read_at, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s, "
                "CASE WHEN %s THEN now() END, now(), now())",
                (uuid4(), lines[seq], channel, pod, seq, user_id, was_read),
            )
        read_numbers = [
            (owner, 0, 0),
            (people["named"], 0, 0),
            (people["read"], 0, 0),
            (people["moved"], 2, 1),
        ]
        for user_id, last_read_seq, read_replies in read_numbers:
            run(
                "INSERT INTO channel_read_state (user_id, channel_id, pod_id, "
                "last_read_seq, read_replies) VALUES (%s, %s, %s, %s, %s)",
                (user_id, channel, pod, last_read_seq, read_replies),
            )
    return {"owner": owner, **people}


def _read_numbers(plain_url: str, people: dict[str, UUID]) -> dict[str, tuple]:
    by_id = {user_id: name for name, user_id in people.items()}
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            "SELECT user_id, last_read_seq, read_replies FROM channel_read_state"
        ).fetchall()
    return {by_id[user_id]: (seq, replies) for user_id, seq, replies in rows}


def _column(plain_url: str) -> tuple[object, ...] | None:
    with psycopg.connect(plain_url) as connection:
        return connection.execute(
            "SELECT data_type, is_nullable, column_default "
            "FROM information_schema.columns "
            "WHERE table_name = 'channels' AND column_name = 'answers_shared'"
        ).fetchone()


def _groups_shared(plain_url: str) -> list[bool]:
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            "SELECT answers_shared FROM channels WHERE kind = 'GROUP'"
        ).fetchall()
    return [row[0] for row in rows]


def test_every_group_shows_answers_after_it_and_it_rolls_back() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        assert _column(plain_url) is None
        people = _a_group(plain_url)

        _migrate(database_url, "upgrade", REVISION)
        assert _column(plain_url) == ("boolean", "NO", "true")
        assert _groups_shared(plain_url) == [True], "the group there takes it on"
        moved_on = {
            # From just before line 4, the earliest unread naming them, with
            # the one reply up to there read.
            "named": (3, 1),
            "owner": (0, 0),  # a seat: their lines always counted
            "read": (0, 0),  # nothing naming them unread: counts nothing
            "moved": (2, 1),  # already moved by reading
        }
        assert _read_numbers(plain_url, people) == moved_on

        _migrate(database_url, "downgrade", BEFORE)
        assert _column(plain_url) is None
        assert _read_numbers(plain_url, people) == moved_on, "left where it is"

        _migrate(database_url, "upgrade", REVISION)
        assert _column(plain_url) == ("boolean", "NO", "true")
        assert _groups_shared(plain_url) == [True]
        assert _read_numbers(plain_url, people) == moved_on
