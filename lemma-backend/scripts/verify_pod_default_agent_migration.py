#!/usr/bin/env python3
"""Seed a pod at 0024, then prove what 0025 does to it -- both ways.

`0025_pod_default_agent_row` rewrites every `agent_conversations`,
`agent_runs` and `agent_surfaces` row a pod owns, which makes it the riskiest
operation in its branch and the one nothing else covers:
`verify_reliability_migration.py` seeds none of these tables with the shapes
that matter here (an agentless surface, a conversation naming nobody).

So this seeds them deliberately at 0024 -- a pod, a named agent, an agentless
Slack surface with channel routes, the pod's own mailbox, conversations and runs
that name nobody, and a schedule -- and asserts the state on the other side, on
the way back down, and after a second trip up.
"""

from __future__ import annotations

import os
from urllib.parse import urlsplit

import psycopg
from alembic import command
from alembic.config import Config

BEFORE = "0024_drop_surface_schedule_id"

USER = "00000000-0000-0000-0000-0000000000a1"
ORG = "00000000-0000-0000-0000-0000000000a2"
POD = "00000000-0000-0000-0000-0000000000a3"
NAMED_AGENT = "00000000-0000-0000-0000-0000000000a4"
SLACK_SURFACE = "00000000-0000-0000-0000-0000000000a5"
MAILBOX = "00000000-0000-0000-0000-0000000000a6"
NAMED_SURFACE = "00000000-0000-0000-0000-0000000000a7"
ASSISTANT_CONV = "00000000-0000-0000-0000-0000000000a8"
NAMED_CONV = "00000000-0000-0000-0000-0000000000a9"
ASSISTANT_RUN = "00000000-0000-0000-0000-0000000000aa"
NAMED_SCHEDULE = "00000000-0000-0000-0000-0000000000ab"

SEED = f"""
INSERT INTO users (email, is_verified, is_active, is_superuser, is_deleted,
                   id, created_at, updated_at)
VALUES ('pod-default-migration@example.com', true, true, false, false,
        '{USER}', now(), now());

INSERT INTO organizations (name, slug, join_policy, id, created_at, updated_at)
VALUES ('Pod Default Migration', 'pod-default-migration', 'INVITE_ONLY',
        '{ORG}', now(), now());

INSERT INTO pods (user_id, organization_id, name, config, is_deleted,
                  id, created_at, updated_at)
VALUES ('{USER}', '{ORG}', 'Pod Default Migration Pod', '{{}}', false,
        '{POD}', now(), now());

INSERT INTO agents (id, pod_id, user_id, name, instruction, toolsets,
                    visibility, created_at, updated_at)
VALUES ('{NAMED_AGENT}', '{POD}', '{USER}', 'triage',
        'You triage tickets.', '[]'::jsonb, 'POD', now(), now());

-- The shape the whole migration exists for: a surface owned by nobody, whose
-- channel config names an agent, which is how one bot used to serve several.
INSERT INTO agent_surfaces (id, pod_id, agent_id, surface_type, name, config,
                            created_at, updated_at)
VALUES ('{SLACK_SURFACE}', '{POD}', NULL, 'SLACK', 'slack',
        '{{"channels": [{{"channel_id": "C1", "channel_name": "sales",
                        "agent_name": "triage"}}]}}'::jsonb,
        now(), now());

-- The pod's own mailbox, minted at pod creation and equally agentless.
INSERT INTO agent_surfaces (id, pod_id, agent_id, surface_type, name, config,
                            created_at, updated_at)
VALUES ('{MAILBOX}', '{POD}', NULL, 'RESEND', 'resend', '{{}}'::jsonb,
        now(), now());

-- And one that already belongs to the named agent, to prove the backfill only
-- touches the nulls.
INSERT INTO agent_surfaces (id, pod_id, agent_id, surface_type, name, config,
                            created_at, updated_at)
VALUES ('{NAMED_SURFACE}', '{POD}', '{NAMED_AGENT}', 'TELEGRAM', 'telegram',
        '{{}}'::jsonb, now(), now());

-- A conversation that named nobody *is* the assistant, pre-0025.
INSERT INTO agent_conversations (id, user_id, pod_id, agent_id,
                                 conversation_type, status, created_at,
                                 updated_at)
VALUES ('{ASSISTANT_CONV}', '{USER}', '{POD}', NULL, 'AGENT', 'IDLE',
        now(), now());

INSERT INTO agent_conversations (id, user_id, pod_id, agent_id,
                                 conversation_type, status, created_at,
                                 updated_at)
VALUES ('{NAMED_CONV}', '{USER}', '{POD}', '{NAMED_AGENT}', 'AGENT', 'IDLE',
        now(), now());

INSERT INTO agent_runs (id, conversation_id, agent_id, status, agent_runtime,
                        started_at, created_at, updated_at)
VALUES ('{ASSISTANT_RUN}', '{ASSISTANT_CONV}', NULL, 'COMPLETED', '{{}}'::jsonb,
        now(), now(), now());

INSERT INTO schedules (id, user_id, pod_id, name, schedule_type, agent_id,
                       config, visibility, is_active, is_internal,
                       consecutive_failures, created_at, updated_at)
VALUES ('{NAMED_SCHEDULE}', '{USER}', '{POD}', 'triage-nightly', 'TIME',
        '{NAMED_AGENT}', '{{}}'::jsonb, 'POD', true, false, 0, now(), now());
"""


def _sync_url(url: str) -> str:
    return url.replace("postgresql+asyncpg://", "postgresql://", 1)


def _refuses(cursor, statement: str, *, what: str) -> None:
    """Assert the database itself rejects `statement`.

    Wrapped in a savepoint: a failed statement poisons the transaction, and the
    next assertion has to be able to run.
    """
    cursor.execute("SAVEPOINT guard")
    try:
        cursor.execute(statement)
    except psycopg.errors.CheckViolation:
        cursor.execute("ROLLBACK TO SAVEPOINT guard")
        return
    raise AssertionError(f"the database allowed {what}, which must be refused")


def _after_upgrade(cursor) -> None:
    cursor.execute(
        "SELECT id::text, name, kind, instruction, visibility "
        "FROM agents WHERE pod_id = %s AND kind = 'POD_DEFAULT'",
        (POD,),
    )
    assert cursor.fetchall() == [(POD, "pod_default", "POD_DEFAULT", "", "POD")], (
        "the pod's assistant must exist exactly once, carrying the pod's own id"
    )

    cursor.execute(
        "SELECT id::text, agent_id::text FROM agent_conversations "
        "WHERE pod_id = %s ORDER BY id",
        (POD,),
    )
    assert cursor.fetchall() == [
        (ASSISTANT_CONV, POD),
        (NAMED_CONV, NAMED_AGENT),
    ], "a conversation that named nobody now names the assistant; others unmoved"

    cursor.execute(
        "SELECT agent_id::text FROM agent_runs WHERE id = %s", (ASSISTANT_RUN,)
    )
    assert cursor.fetchone() == (POD,), "the run follows its conversation"

    cursor.execute(
        "SELECT id::text, agent_id::text FROM agent_surfaces "
        "WHERE pod_id = %s ORDER BY id",
        (POD,),
    )
    assert cursor.fetchall() == [
        (SLACK_SURFACE, POD),
        (MAILBOX, POD),
        (NAMED_SURFACE, NAMED_AGENT),
    ], "every agentless surface becomes the assistant's; the owned one is left"

    cursor.execute(
        "SELECT is_nullable FROM information_schema.columns "
        "WHERE table_name = 'agent_surfaces' AND column_name = 'agent_id'"
    )
    assert cursor.fetchone() == ("NO",), "agent_id must be NOT NULL after 0025"

    cursor.execute(
        "SELECT conname FROM pg_constraint WHERE conname IN ("
        "'ck_agents_pod_default_is_pod_id', "
        "'ck_agents_name_not_pod_default_selector', "
        "'ck_agents_pod_default_immutable', "
        "'ck_schedules_single_target') ORDER BY conname"
    )
    assert [row[0] for row in cursor.fetchall()] == [
        "ck_agents_name_not_pod_default_selector",
        "ck_agents_pod_default_immutable",
        "ck_agents_pod_default_is_pod_id",
        "ck_schedules_single_target",
    ], "all four constraints must be present"

    # The convention is only worth anything if the database enforces it.
    _refuses(
        cursor,
        f"UPDATE agents SET instruction = 'edited' WHERE id = '{POD}'",
        what="editing the assistant's instruction",
    )
    _refuses(
        cursor,
        f"UPDATE agents SET name = 'renamed' WHERE id = '{POD}'",
        what="renaming the assistant",
    )
    _refuses(
        cursor,
        f"UPDATE agents SET kind = 'POD_DEFAULT' WHERE id = '{NAMED_AGENT}'",
        what="a second POD_DEFAULT whose id is not the pod's",
    )
    _refuses(
        cursor,
        f"UPDATE agents SET name = 'pod_default' WHERE id = '{NAMED_AGENT}'",
        what="a user agent taking the assistant's reserved name",
    )


def _cascade_leaves_the_assistant_alone(cursor) -> None:
    """The bug the old `ON DELETE SET NULL` caused, now impossible.

    A deleted agent's mailbox used to become an agentless surface -- which is
    what the assistant's own surface looked like -- so the pod started answering
    from a dead agent's address.
    """
    cursor.execute("DELETE FROM agents WHERE id = %s", (NAMED_AGENT,))
    cursor.execute(
        "SELECT id::text FROM agent_surfaces WHERE pod_id = %s ORDER BY id", (POD,)
    )
    assert cursor.fetchall() == [(SLACK_SURFACE,), (MAILBOX,)], (
        "the deleted agent's surface goes with it, rather than becoming a "
        "second surface belonging to the assistant"
    )
    # The schedule does *not* go with it. It used to: `schedules.agent_id` was
    # ON DELETE CASCADE, so deleting an agent silently took every schedule
    # pointing at it and the whole run ledger underneath — the evidence of what
    # had already happened, removed along with the thing that would happen next.
    # It is SET NULL now, so the row survives naming nobody; the fire path
    # dead-letters it and pauses it rather than failing forever in silence.
    cursor.execute("SELECT agent_id FROM schedules WHERE id = %s", (NAMED_SCHEDULE,))
    assert cursor.fetchone() == (None,), (
        "the schedule outlives the agent it named, holding no agent, so its "
        "history is not deleted with its target"
    )


def _after_downgrade(cursor) -> None:
    cursor.execute("SELECT count(*) FROM agents WHERE id = %s", (POD,))
    assert cursor.fetchone() == (0,), "the assistant's row is removed"

    cursor.execute(
        "SELECT agent_id FROM agent_conversations WHERE id = %s", (ASSISTANT_CONV,)
    )
    assert cursor.fetchone() == (None,), "the conversation names nobody again"

    cursor.execute("SELECT agent_id FROM agent_runs WHERE id = %s", (ASSISTANT_RUN,))
    assert cursor.fetchone() == (None,), "and so does its run"

    cursor.execute(
        "SELECT agent_id FROM agent_surfaces WHERE id = %s", (SLACK_SURFACE,)
    )
    assert cursor.fetchone() == (None,), "the surface is agentless again"

    cursor.execute(
        "SELECT column_name FROM information_schema.columns "
        "WHERE (table_name, column_name) IN "
        "(('agents', 'kind'), ('schedules', 'instruction'))"
    )
    assert cursor.fetchall() == [], "both added columns are gone"


def main() -> int:
    url = os.environ.get("MIGRATION_TEST_DATABASE_URL")
    if not url:
        raise RuntimeError("MIGRATION_TEST_DATABASE_URL is required")
    database = urlsplit(_sync_url(url)).path.lstrip("/")
    if "migration_test" not in database:
        raise RuntimeError("Refusing to mutate a database not named migration_test")

    # A clean slate, because this runs after another verifier in the same job
    # and needs to walk up from 0024 rather than from whatever it left behind.
    with psycopg.connect(_sync_url(url), autocommit=True) as connection:
        connection.execute("DROP SCHEMA public CASCADE")
        connection.execute("CREATE SCHEMA public")

    os.environ["DATABASE_URL"] = url
    config = Config("alembic.ini")
    command.upgrade(config, BEFORE)

    with psycopg.connect(_sync_url(url)) as connection, connection.cursor() as cursor:
        cursor.execute(SEED)

    command.upgrade(config, "head")
    with psycopg.connect(_sync_url(url)) as connection, connection.cursor() as cursor:
        _after_upgrade(cursor)
        _cascade_leaves_the_assistant_alone(cursor)
        connection.rollback()  # keep the seed intact for the downgrade

    command.downgrade(config, BEFORE)
    with psycopg.connect(_sync_url(url)) as connection, connection.cursor() as cursor:
        _after_downgrade(cursor)

    # Re-upgrading must land in the same place: the insert is ON CONFLICT DO
    # NOTHING and the backfills are idempotent, which is what makes a resumed
    # deploy safe.
    command.upgrade(config, "head")
    with psycopg.connect(_sync_url(url)) as connection, connection.cursor() as cursor:
        _after_upgrade(cursor)

    print("Pod default agent migration verification passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
