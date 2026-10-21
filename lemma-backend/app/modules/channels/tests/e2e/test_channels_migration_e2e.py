"""Migration 0058 replaces conversation links and group tables with channels.

Groups and web chat were never released, and a member's private chat starts
afresh in their direct channel, so nothing is carried over: the upgrade drops
the link and group tables and makes the channel ones, and the downgrade makes
the old tables again, empty. What is checked is the schema each way -- the
index that keeps one conversation per person, agent and channel, and the
columns and tables the channel app will build on without a migration of its
own -- and that every member already in a pod has their own channel when the
upgrade is done, as joining a pod makes it from then on.
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
BEFORE = "0057_contacts_for_everyone"
AFTER = "0058_channels"

_OLD = (
    "agent_surface_conversation_links",
    "agent_surface_groups",
    "agent_surface_group_messages",
    "notifications",
)
_NEW = (
    "external_accounts",
    "channels",
    "channel_cases",
    "channel_case_events",
    "channel_members",
    "channel_read_state",
    "channel_sources",
    "channel_messages",
    "channel_mentions",
    "channel_questions",
    "channel_message_deliveries",
    "channel_line_answers",
    "channel_delivery_attempts",
    "channel_push_pending",
    "channel_changes",
    "channel_threads",
)


def _seed_members(plain_url: str) -> dict[UUID, set[UUID]]:
    """Two pods of one organization at 0057: Asha in both, Ben in one, and
    Chen in the organization but in neither. Each pod's members, by pod."""
    org, asha, ben, chen = uuid4(), uuid4(), uuid4(), uuid4()
    pods = {uuid4(): {asha, ben}, uuid4(): {asha}}
    with psycopg.connect(plain_url) as connection:
        run = connection.execute
        run(
            "INSERT INTO organizations (id, name, slug, join_policy, created_at, "
            "updated_at) VALUES (%s, 'Org', %s, 'INVITE_ONLY', now(), now())",
            (org, f"org-{org.hex[:8]}"),
        )
        members: dict[UUID, UUID] = {}
        for user_id in (asha, ben, chen):
            run(
                "INSERT INTO users (id, email, is_verified, is_active, "
                "is_superuser, is_deleted, created_at, updated_at) "
                "VALUES (%s, %s, true, true, false, false, now(), now())",
                (user_id, f"{user_id.hex[:8]}@example.com"),
            )
            members[user_id] = uuid4()
            run(
                "INSERT INTO organization_members (id, user_id, organization_id, "
                "role, created_at, updated_at) "
                "VALUES (%s, %s, %s, 'ORG_MEMBER', now(), now())",
                (members[user_id], user_id, org),
            )
        for pod_id, people in pods.items():
            run(
                "INSERT INTO pods (id, user_id, organization_id, name, config, "
                "is_deleted, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, '{}', false, now(), now())",
                (pod_id, asha, org, f"Pod {pod_id.hex[:8]}"),
            )
            for user_id in people:
                run(
                    "INSERT INTO pod_members (id, pod_id, organization_member_id, "
                    "created_at, updated_at) VALUES (%s, %s, %s, now(), now())",
                    (uuid4(), pod_id, members[user_id]),
                )
    return pods


def _own_channels(plain_url: str) -> dict[UUID, set[UUID]]:
    """Each pod's DIRECT channels' members, by pod -- one channel each, with
    its member seated as its owner."""
    with psycopg.connect(plain_url) as connection:
        rows = connection.execute(
            "SELECT c.pod_id, c.member_user_id, c.visibility, m.user_id, m.role "
            "FROM channels c LEFT JOIN channel_members m ON m.channel_id = c.id "
            "WHERE c.kind = 'DIRECT'"
        ).fetchall()
    found: dict[UUID, set[UUID]] = {}
    for pod_id, owner, visibility, seated, role in rows:
        assert (visibility, seated, role) == ("PRIVATE", owner, "owner"), rows
        assert owner not in found.get(pod_id, set()), "two channels for one member"
        found.setdefault(pod_id, set()).add(owner)
    return found


def test_channels_replace_links_and_groups_and_roll_back_cleanly() -> None:
    with get_postgres_container() as postgres:
        database_url = get_postgres_url(postgres)
        environment = {**os.environ, "DATABASE_URL": database_url}
        plain_url = database_url.replace("postgresql+asyncpg", "postgresql")

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

        def present(*tables: str) -> set[str]:
            with psycopg.connect(plain_url) as connection:
                return {
                    table
                    for table in tables
                    if connection.execute("SELECT to_regclass(%s)", [table]).fetchone()
                    != (None,)
                }

        def columns(table: str) -> set[str]:
            with psycopg.connect(plain_url) as connection:
                rows = connection.execute(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_name = %s",
                    [table],
                ).fetchall()
            return {row[0] for row in rows}

        def constraints() -> set[str]:
            with psycopg.connect(plain_url) as connection:
                rows = connection.execute(
                    "SELECT conname FROM pg_constraint"
                ).fetchall()
            return {row[0] for row in rows}

        def storage(table: str) -> set[str]:
            with psycopg.connect(plain_url) as connection:
                row = connection.execute(
                    "SELECT reloptions FROM pg_class WHERE relname = %s", [table]
                ).fetchone()
            return set(row[0] or []) if row else set()

        def index(name: str) -> str | None:
            with psycopg.connect(plain_url) as connection:
                row = connection.execute(
                    "SELECT indexdef FROM pg_indexes WHERE indexname = %s", [name]
                ).fetchone()
            return row[0] if row else None

        migrate("upgrade", BEFORE)
        assert present(*_OLD) == set(_OLD)
        assert not present(*_NEW)
        pods = _seed_members(plain_url)

        migrate("upgrade", AFTER)
        # Every member already in a pod has their own channel there; somebody
        # in the organization but in no pod has none.
        assert _own_channels(plain_url) == pods
        assert not present(*_OLD), "the link and group tables outlived channels"
        assert present(*_NEW) == set(_NEW)
        # A place belongs to an account; a group's bot-side settings and a
        # place's address live on its source.
        assert {
            "account_id",
            "is_private",
            "delivery_ref",
            "owner_user_id",
            "answers_outsiders",
            "request_id",
        } <= columns("channel_sources")
        # Who may see a channel is its own column; a contact's is answered in
        # cases, not settings; the timeline's head is a number.
        assert {
            "visibility",
            "agents_answer",
            "hand_back_after_hours",
            "current_case_id",
            "last_seq",
            "last_line_id",
            "last_line_at",
        } <= columns("channels")
        assert not {"status", "assignee_user_id", "attention_at"} & columns("channels")
        assert {
            "status",
            "assignee_user_id",
            "attention_at",
            "acknowledged_at",
            "first_response_at",
            "closed_at",
        } <= columns("channel_cases")
        assert index("uq_channel_cases_open") is not None
        assert {
            "seq",
            "client_message_id",
            "parts",
            "search",
            "thread_root_id",
            "reply_to_id",
            "edited_at",
            "deleted_at",
        } <= columns("channel_messages")
        assert {"role", "notify", "notify_via", "muted_until", "answers"} <= columns(
            "channel_members"
        )
        assert {"ref", "kind", "state", "batch_id", "expires_at"} <= columns(
            "channel_questions"
        )
        assert index("uq_channel_messages_client") is not None
        # What a live subscriber resumes from is the change log, not a number
        # on every line; nothing indexes what a post moves on its channel.
        assert "rev" not in columns("channel_messages")
        assert index("ix_channel_messages_rev") is None
        assert index("ix_channels_pod_updated") is None
        assert index("ix_channel_changes_pod_at") is not None
        assert {"last_spoke_at", "last_name"} <= columns("channel_members")
        assert storage("channels") == {"fillfactor=80"}
        assert storage("channel_messages") == {
            "autovacuum_vacuum_scale_factor=0.02",
            "autovacuum_vacuum_insert_scale_factor=0.02",
        }
        # Who is seated why; read state is the pod's; a question names its
        # asker by type and id, and its ref is one unique key, no prefix index.
        assert "seated_by" in columns("channel_members")
        assert {"pod_id", "created_at"} <= columns("channel_read_state")
        assert {
            "asker",
            "asker_conversation_id",
            "asker_workflow_run_id",
            "asker_key",
            "awaits_policy",
        } <= columns("channel_questions")
        assert index("ix_channel_questions_ref_prefix") is None
        assert index("ix_channel_members_channel") is not None
        external = index("uq_channel_messages_external")
        assert external is not None and "::uuid" in external, external
        assert {
            "uq_channel_messages_id_channel",
            "uq_channel_messages_id_channel_seq",
            "fk_channel_messages_thread_root",
            "fk_channel_messages_source",
            "fk_channel_mentions_line",
            "fk_channel_questions_line",
        } <= constraints()
        assert index("ix_channel_mentions_user") is not None
        assert "channel_id" in columns("agent_conversations")
        # Who answers a line, held to the line; a conversation's channel is
        # one of its own pod's.
        assert {
            "fk_channel_line_answers_line",
            "fk_agent_conversations_channel",
        } <= constraints()
        one_each = index("uq_agent_conversations_channel_person")
        assert one_each is not None and "UNIQUE" in one_each, one_each
        assert index("uq_channel_sources_place") is not None
        # An account is a workspace and, once known, its bot; a place's
        # group settings are a chat's alone.
        assert {"workspace_key", "bot_key"} <= columns("external_accounts")
        assert "account_key" not in columns("external_accounts")
        assert {
            "uq_external_accounts_key",
            "uq_channel_sources_id_channel",
            "ck_channel_sources_kind",
            "ck_channel_sources_settings_on_a_chat",
        } <= constraints()
        assert index("ix_channel_sources_account") is not None
        assert "WHERE" in (index("ix_channel_sources_shared") or "")

        migrate("downgrade", BEFORE)
        assert present(*_OLD) == set(_OLD)
        assert not present(*_NEW)
        assert "channel_id" not in columns("agent_conversations")

        # Upgrading again makes them again, once each.
        migrate("upgrade", AFTER)
        assert _own_channels(plain_url) == pods
        migrate("upgrade", "head")
