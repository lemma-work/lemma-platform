"""Migration 0073: a person's conversation with an agent is kept per platform.

Seeded before it, in one member's DIRECT channel, with a WhatsApp place and an
email place: four conversations, each with its own agent (the old index held
a person to one per agent in a channel). The first was spoken to on WhatsApp,
then by email, then on WhatsApp again; the second on WhatsApp and then in
Lemma itself; the third only by a mail its agent sent; the fourth, a question
pushed on Telegram, by nobody yet. After it, each is on the platform it was
last spoken on -- WhatsApp, none, email -- or opened on, Telegram; and the
first agent can open a second conversation with the member by email. The
downgrade keeps the most recently active of the two and archives the other;
upgrading again succeeds.
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


_SEEDED = ("on_whatsapp", "in_lemma", "mailed", "asked")


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


def _seed(plain_url: str) -> dict[str, UUID]:
    names = (
        "org",
        "user",
        "pod",
        "channel",
        "whatsapp_account",
        "email_account",
        "whatsapp",
        "email",
        "agent_1",
        "agent_2",
        "agent_3",
        "agent_4",
        "on_whatsapp",
        "in_lemma",
        "mailed",
        "asked",
    )
    ids = {name: uuid4() for name in names}
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
        for agent in ("agent_1", "agent_2", "agent_3", "agent_4"):
            run(
                "INSERT INTO agents (id, pod_id, user_id, name, kind, visibility, "
                "instruction, toolsets, created_at, updated_at) VALUES "
                "(%s, %s, %s, %s, 'USER', 'POD', 'Help.', '[]', now(), now())",
                (ids[agent], ids["pod"], ids["user"], agent),
            )
        run(
            "INSERT INTO channels (id, pod_id, kind, visibility, member_user_id, "
            "created_at, updated_at) VALUES "
            "(%s, %s, 'DIRECT', 'PRIVATE', %s, now(), now())",
            (ids["channel"], ids["pod"], ids["user"]),
        )
        for place, platform in (("whatsapp", "WHATSAPP"), ("email", "RESEND")):
            account = ids[f"{place}_account"]
            run(
                "INSERT INTO external_accounts (id, pod_id, platform, workspace_key, "
                "created_at, updated_at) VALUES (%s, %s, %s, %s, now(), now())",
                (account, ids["pod"], platform, place),
            )
            run(
                "INSERT INTO channel_sources (id, channel_id, pod_id, account_id, "
                "platform, kind, external_user_id, created_at, updated_at) VALUES "
                "(%s, %s, %s, %s, %s, 'PERSON', %s, now(), now())",
                (ids[place], ids["channel"], ids["pod"], account, platform, place),
            )
        for conversation, agent in (
            ("on_whatsapp", "agent_1"),
            ("in_lemma", "agent_2"),
            ("mailed", "agent_3"),
            ("asked", "agent_4"),
        ):
            run(
                "INSERT INTO agent_conversations (id, user_id, pod_id, agent_id, "
                "channel_id, conversation_type, created_at, updated_at) VALUES "
                "(%s, %s, %s, %s, %s, 'CHAT', now(), now())",
                (
                    ids[conversation],
                    ids["user"],
                    ids["pod"],
                    ids[agent],
                    ids["channel"],
                ),
            )
        # (conversation, heard at, carried to, said by the member): in order.
        lines = (
            ("on_whatsapp", "whatsapp", None, True),
            ("on_whatsapp", "email", None, True),
            ("on_whatsapp", None, "email", False),
            ("on_whatsapp", "whatsapp", None, True),
            ("on_whatsapp", None, "whatsapp", False),
            ("in_lemma", "whatsapp", None, True),
            ("in_lemma", None, None, True),
            ("mailed", None, "email", False),
        )
        for seq, (conversation, heard, carried, by_member) in enumerate(lines, 1):
            run(
                "INSERT INTO channel_messages (id, channel_id, pod_id, seq, "
                "sender_kind, sender_user_id, body, source_id, target_source_id, "
                "conversation_id, created_at, updated_at) VALUES (%s, %s, %s, %s, "
                "%s, %s, 'x', %s, %s, %s, now() + make_interval(secs => %s), now())",
                (
                    uuid4(),
                    ids["channel"],
                    ids["pod"],
                    seq,
                    "MEMBER" if by_member else "AGENT",
                    ids["user"] if by_member else None,
                    ids[heard] if heard else None,
                    ids[carried] if carried else None,
                    ids[conversation],
                    seq,
                ),
            )
        # Opened by a push on Telegram, with no line yet: its surface state
        # alone says where the answer will come from.
        run(
            "INSERT INTO surface_conversation_state (conversation_id, "
            "surface_platform) VALUES (%s, 'TELEGRAM')",
            (ids["asked"],),
        )
    return ids


def _platforms(plain_url: str, ids: dict[str, UUID]) -> dict[str, str | None]:
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            "SELECT id, platform FROM agent_conversations WHERE id = ANY(%s)",
            ([ids[name] for name in _SEEDED],),
        ).fetchall()
    by_id = {row[0]: row[1] for row in rows}
    return {name: by_id[ids[name]] for name in _SEEDED}


def test_each_conversation_is_seated_where_it_was_last_spoken_at() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")
        _migrate(database_url, "upgrade", BEFORE)
        ids = _seed(plain_url)

        _migrate(database_url, "upgrade", REVISION)

        assert _platforms(plain_url, ids) == {
            "on_whatsapp": "WHATSAPP",
            "in_lemma": None,
            "mailed": "RESEND",
            "asked": "TELEGRAM",
        }
        # The first agent's conversation with the member by email: one of
        # its own, beside the one on WhatsApp.
        by_email = uuid4()
        with psycopg.connect(plain_url) as connection:
            connection.execute(
                "INSERT INTO agent_conversations (id, user_id, pod_id, agent_id, "
                "channel_id, platform, conversation_type, created_at, updated_at, "
                "last_activity_at) VALUES (%s, %s, %s, %s, %s, %s, 'CHAT', now(), "
                "now(), now() + interval '1 hour')",
                (
                    by_email,
                    ids["user"],
                    ids["pod"],
                    ids["agent_1"],
                    ids["channel"],
                    "RESEND",
                ),
            )
        # A second on the same platform is still refused.
        with psycopg.connect(plain_url) as connection:
            with pytest.raises(psycopg.errors.UniqueViolation):
                connection.execute(
                    "INSERT INTO agent_conversations (id, user_id, pod_id, "
                    "agent_id, channel_id, platform, conversation_type, "
                    "created_at, updated_at) VALUES (%s, %s, %s, %s, %s, %s, "
                    "'CHAT', now(), now())",
                    (
                        uuid4(),
                        ids["user"],
                        ids["pod"],
                        ids["agent_1"],
                        ids["channel"],
                        "RESEND",
                    ),
                )

        _migrate(database_url, "downgrade", BEFORE)

        with psycopg.connect(plain_url) as connection:
            archived = dict(
                connection.execute(
                    "SELECT id, is_archived FROM agent_conversations "
                    "WHERE id = ANY(%s)",
                    ([ids["on_whatsapp"], by_email],),
                ).fetchall()
            )
        # The most recently active of the agent's two keeps the seat.
        assert archived == {ids["on_whatsapp"]: True, by_email: False}

        _migrate(database_url, "upgrade", REVISION)
