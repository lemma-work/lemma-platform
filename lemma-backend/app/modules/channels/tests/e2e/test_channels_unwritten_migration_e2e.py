"""Migration 0072 stops requiring two channel columns nothing reads, and its
downgrade fills them back in; upgrading again works.

Upgraded, a case may have no ``opened_seq`` and a waiting push window no
``to_seq``. Downgraded, each gets the value the old schema requires -- the
channel's newest line before the case opened, the window's ``lead_seq`` --
and the old constraints hold again.

The schema the other e2e tests use is built from the models, so this is the
one place the migration itself runs.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest

from app.core.test_utils import get_postgres_container, get_postgres_url

# A walk of the Alembic chain on its own Postgres, like the other chain walks:
# the nightly protected lane runs it.
pytestmark = [pytest.mark.e2e, pytest.mark.slow]

BACKEND = Path(__file__).resolve().parents[5]
BEFORE = "0071_asked_at_source"
REVISION = "0072_channels_unwritten"


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


def _schema(sync_url: str) -> tuple[str, str]:
    """Whether ``opened_seq`` may be empty, and the push window's check."""
    with psycopg.connect(sync_url) as connection:
        nullable = connection.execute(
            "SELECT is_nullable FROM information_schema.columns"
            " WHERE table_name = 'channel_cases' AND column_name = 'opened_seq'"
        ).fetchone()
        check = connection.execute(
            "SELECT pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conname = 'ck_channel_push_pending_window'"
        ).fetchone()
    assert nullable is not None and check is not None
    return nullable[0], check[0]


def _seed_unwritten(sync_url: str) -> None:
    """A case and a waiting window as code that no longer writes the two
    columns leaves them. Foreign keys are not under test."""
    channel, pod, user = uuid4(), uuid4(), uuid4()
    with psycopg.connect(sync_url) as connection:
        run = connection.execute
        run("SET session_replication_role = 'replica'")
        run(
            "INSERT INTO channel_cases (id, channel_id, pod_id, opened_at,"
            " created_at, updated_at) VALUES (%s, %s, %s, now(), now(), now())",
            (uuid4(), channel, pod),
        )
        run(
            "INSERT INTO channel_push_pending (channel_id, recipient_user_id,"
            " pod_id, from_seq, lead_seq, lines, next_attempt_at)"
            " VALUES (%s, %s, %s, 3, 5, 2, now())",
            (channel, user, pod),
        )


def _filled(sync_url: str) -> tuple[object, object]:
    with psycopg.connect(sync_url) as connection:
        case = connection.execute("SELECT opened_seq FROM channel_cases").fetchone()
        window = connection.execute(
            "SELECT to_seq FROM channel_push_pending"
        ).fetchone()
    assert case is not None and window is not None
    return case[0], window[0]


def test_two_unread_columns_stop_being_required_and_come_back_filled() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        sync_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        nullable, check = _schema(sync_url)
        assert nullable == "NO" and "to_seq" in check

        _migrate(database_url, "upgrade", REVISION)
        nullable, check = _schema(sync_url)
        assert nullable == "YES" and "to_seq" not in check
        _seed_unwritten(sync_url)

        _migrate(database_url, "downgrade", BEFORE)
        nullable, check = _schema(sync_url)
        assert nullable == "NO" and "to_seq" in check
        # No line came before the case opened; the window runs to its lead.
        assert _filled(sync_url) == (0, 5)

        _migrate(database_url, "upgrade", REVISION)
        nullable, check = _schema(sync_url)
        assert nullable == "YES" and "to_seq" not in check
