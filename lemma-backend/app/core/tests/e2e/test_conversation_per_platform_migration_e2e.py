"""Migration 0073: a person's conversation with an agent is kept per platform.

Seeded before it: one member's DIRECT channel and a conversation of theirs
with an agent. After it, that conversation has no platform -- nothing is
backfilled, so it reads as kept in Lemma -- and the agent can open a second
conversation with the member by email, but not a third. The downgrade keeps
the most recently active of the two and archives the other; upgrading again
succeeds.
"""

from __future__ import annotations

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

BACKEND = Path(__file__).resolve().parents[4]
BEFORE = "0072_workflow_runs_by_time"
REVISION = "0073_conversation_per_platform"


def _migrate(
    database_url: str, direction: str, revision: str, *, succeeds: bool = True
) -> None:
    result = subprocess.run(
        ["uv", "run", "--no-sync", "alembic", direction, revision],
        cwd=BACKEND,
        env={**os.environ, "DATABASE_URL": database_url},
        capture_output=True,
        text=True,
        timeout=300,
    )
    assert (result.returncode == 0) == succeeds, (
        result.stdout[-3000:] + result.stderr[-3000:]
    )


def _seed(plain_url: str) -> dict[str, UUID]:
    """A member with a direct channel to an agent, and their conversation
    there from before the platform column (``old``)."""
    ids = {name: uuid4() for name in ("org", "user", "pod", "channel", "agent", "old")}
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
            (ids["user"], f"{ids['user'].hex[:8]}@example.com"),
        )
        run(
            "INSERT INTO pods (id, user_id, organization_id, name, config, "
            "is_deleted, created_at, updated_at) "
            "VALUES (%s, %s, %s, 'Pod', '{}', false, now(), now())",
            (ids["pod"], ids["user"], ids["org"]),
        )
        run(
            "INSERT INTO agents (id, pod_id, user_id, name, kind, visibility, "
            "instruction, toolsets, created_at, updated_at) VALUES "
            "(%s, %s, %s, 'agent', 'USER', 'POD', 'Help.', '[]', now(), now())",
            (ids["agent"], ids["pod"], ids["user"]),
        )
        run(
            "INSERT INTO channels (id, pod_id, kind, visibility, member_user_id, "
            "created_at, updated_at) VALUES "
            "(%s, %s, 'DIRECT', 'PRIVATE', %s, now(), now())",
            (ids["channel"], ids["pod"], ids["user"]),
        )
        run(
            "INSERT INTO agent_conversations (id, user_id, pod_id, agent_id, "
            "channel_id, conversation_type, created_at, updated_at) VALUES "
            "(%s, %s, %s, %s, %s, 'CHAT', now(), now())",
            (ids["old"], ids["user"], ids["pod"], ids["agent"], ids["channel"]),
        )
    return ids


def _open_by_email(plain_url: str, ids: dict[str, UUID]) -> UUID:
    """Open the member's email conversation in the same channel, active
    after ``old`` so a downgrade keeps it."""
    conversation = uuid4()
    with psycopg.connect(plain_url) as connection:
        connection.execute(
            "INSERT INTO agent_conversations (id, user_id, pod_id, agent_id, "
            "channel_id, platform, conversation_type, created_at, updated_at, "
            "last_activity_at) VALUES (%s, %s, %s, %s, %s, 'RESEND', 'CHAT', "
            "now(), now(), now() + interval '1 hour')",
            (conversation, ids["user"], ids["pod"], ids["agent"], ids["channel"]),
        )
    return conversation


def test_a_person_gets_a_conversation_per_platform() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)

        _migrate(database_url, "upgrade", REVISION)

        with psycopg.connect(plain_url) as connection:
            [(platform,)] = connection.execute(
                "SELECT platform FROM agent_conversations WHERE id = %s",
                (ids["old"],),
            ).fetchall()
        assert platform is None  # not backfilled: it reads as kept in Lemma
        by_email = _open_by_email(plain_url, ids)
        # A second on the same platform is still refused.
        with pytest.raises(psycopg.errors.UniqueViolation):
            _open_by_email(plain_url, ids)

        _migrate(database_url, "downgrade", BEFORE)

        with psycopg.connect(plain_url) as connection:
            archived = dict(
                connection.execute(
                    "SELECT id, is_archived FROM agent_conversations "
                    "WHERE id = ANY(%s)",
                    ([ids["old"], by_email],),
                ).fetchall()
            )
        # The most recently active of the two keeps the seat.
        assert archived == {ids["old"]: True, by_email: False}

        _migrate(database_url, "upgrade", REVISION)


def test_an_interrupted_upgrade_reruns_cleanly() -> None:
    """A build that fails leaves the column committed, the old index dropped
    and the new one half-built, with 0073 unstamped; the rerun adds no column
    twice, drops the half-built index and builds it again."""
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)
        # Two live conversations on one platform -- possible only once the old
        # index is gone -- so the unique build fails part way.
        with psycopg.connect(plain_url) as connection:
            connection.execute(
                "DROP INDEX uq_agent_conversations_channel_person;"
                "ALTER TABLE agent_conversations ADD COLUMN platform VARCHAR(32)"
            )
        doubled = _open_by_email(plain_url, ids)
        _open_by_email(plain_url, ids)

        _migrate(database_url, "upgrade", REVISION, succeeds=False)

        with psycopg.connect(plain_url) as connection:
            [(valid,)] = connection.execute(
                "SELECT i.indisvalid FROM pg_index i JOIN pg_class c "
                "ON c.oid = i.indexrelid "
                "WHERE c.relname = 'uq_agent_conversations_channel_person_platform'"
            ).fetchall()
            assert not valid
            connection.execute(
                "UPDATE agent_conversations SET is_archived = true WHERE id = %s",
                (doubled,),
            )

        _migrate(database_url, "upgrade", REVISION)

        with pytest.raises(psycopg.errors.UniqueViolation):
            _open_by_email(plain_url, ids)
