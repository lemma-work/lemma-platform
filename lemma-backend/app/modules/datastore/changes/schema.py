"""The change log's storage: two tables per pod schema, one wake table per database.

``reserved_changes`` is the pod's record change log. Every record write appends
one slim row per changed record in the same statement as the write; the row and
the prior values are filled in only while a durable subscriber watches that
``(table, operation)`` and acts on that write's intent -- a trigger that skips
bulk writes is woken for them, to record the skip, but never reads their rows.
``reserved_change_subscribers`` names who watches what and how far each has read.

The ``reserved_`` prefix is the namespace the platform already owns inside a pod
schema (a table name starting with it is refused at creation), so neither table
can collide with a table a person makes, and neither is reachable through the
read-only query endpoint, which only resolves registered tables. The query role
is also denied both outright, because default privileges would otherwise grant
it SELECT on every table created in the schema.

``lemma_change_pending`` lives once in the database's ``public`` schema. A write
that a durable subscriber watches inserts a row naming its pod; the handoff
deletes exactly the rows it read once the deliveries they cause are committed.
A wake is never a flag that a concurrent writer could find already set.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

from app.modules.datastore.infrastructure.query_role import query_role_name

CHANGES_TABLE = "reserved_changes"
SUBSCRIBERS_TABLE = "reserved_change_subscribers"
PENDING_TABLE = "public.lemma_change_pending"
#: Per pod, the newest position retention has deleted through: a cursor below
#: it may have missed changes, so its reader is told to resync.
FLOORS_TABLE = "public.lemma_change_floors"
#: Notified by a watched write's transaction; the handoff listens on it.
PENDING_CHANNEL = "lemma_change_pending"
_ALL_INTENTS = "'{normal,bulk,import}'"


def changes_table(schema: str) -> str:
    return f'"{schema}".{CHANGES_TABLE}'


def subscribers_table(schema: str) -> str:
    return f'"{schema}".{SUBSCRIBERS_TABLE}'


async def create_global_tables(conn: AsyncConnection) -> None:
    """The database-wide wake and floor tables; called under the global DDL lock."""
    await conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {PENDING_TABLE} ("
            "id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY, "
            "pod_id uuid NOT NULL, "
            "at timestamptz NOT NULL DEFAULT now())"
        )
    )
    await conn.execute(
        text(
            f"CREATE TABLE IF NOT EXISTS {FLOORS_TABLE} ("
            "pod_id uuid PRIMARY KEY, "
            "floor_xid xid8 NOT NULL, "
            "floor_seq bigint NOT NULL, "
            "at timestamptz NOT NULL DEFAULT now())"
        )
    )


async def _present(conn: AsyncConnection, schema: str) -> set[str]:
    """Which of the change log's tables, index and late column already exist."""
    row = (
        await conn.execute(
            text(
                "SELECT to_regclass(:changes) IS NOT NULL, "
                "to_regclass(:subscribers) IS NOT NULL, "
                "to_regclass(:table_index) IS NOT NULL, "
                "EXISTS (SELECT 1 FROM information_schema.columns "
                "WHERE table_schema = :schema AND table_name = :subscribers_name "
                "AND column_name = 'snapshot_intents')"
            ),
            {
                "changes": changes_table(schema),
                "subscribers": subscribers_table(schema),
                "table_index": f'"{schema}".ix_reserved_change_subscribers_table',
                "schema": schema,
                "subscribers_name": SUBSCRIBERS_TABLE,
            },
        )
    ).one()
    names = ("changes", "subscribers", "table_index", "intents")
    return {name for name, there in zip(names, row) if there}


async def create_change_log(conn: AsyncConnection, schema: str) -> None:
    """Version 3 of a pod schema: the change log and its subscribers.

    Only what is missing is created. ``IF NOT EXISTS`` alone still locks: an
    index's table is share-locked and ``ALTER TABLE ... ADD COLUMN IF NOT
    EXISTS`` takes an exclusive lock before either finds there is nothing to
    do -- and a schema whose change log a record write already created (ahead
    of the backfill, ``ensure_change_log``) is being written to. Locking its
    tables in DDL order against a writer's order deadlocked the two.
    """
    changes = changes_table(schema)
    subscribers = subscribers_table(schema)
    present = await _present(conn, schema)
    statements: list[str] = []
    if "changes" not in present:
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {changes} ("
            "seq bigint GENERATED ALWAYS AS IDENTITY, "
            # The writing transaction: what makes the read rule in
            # ``reader`` able to wait for a slow writer instead of skipping it.
            "xid xid8 NOT NULL DEFAULT pg_current_xact_id(), "
            # By id, not name, so a renamed table keeps its subscribers.
            "table_id uuid NOT NULL, "
            "op char(1) NOT NULL, "
            # Text: a table's primary key may be a uuid, an integer or text.
            "record_id text NOT NULL, "
            "intent text NOT NULL DEFAULT 'normal', "
            "owner_user_id uuid, "
            "outside_actor text, "
            "new_row jsonb, "
            "old_values jsonb, "
            "at timestamptz NOT NULL DEFAULT now(), "
            "PRIMARY KEY (xid, seq))"
        )
    if "subscribers" not in present:
        statements.append(
            f"CREATE TABLE IF NOT EXISTS {subscribers} ("
            "subscriber text PRIMARY KEY, "
            "table_id uuid NOT NULL, "
            "ops text[] NOT NULL, "
            # How long a wake waits before delivery, so a batching subscriber
            # reads what accumulated rather than one change at a time.
            "delay_ms integer NOT NULL DEFAULT 0, "
            # The write intents whose changes it acts on with their rows; a
            # watched write of any other intent is logged slim.
            f"snapshot_intents text[] NOT NULL DEFAULT {_ALL_INTENTS}, "
            "cursor_xid xid8 NOT NULL, "
            "cursor_seq bigint NOT NULL, "
            "created_at timestamptz NOT NULL DEFAULT now(), "
            "updated_at timestamptz NOT NULL DEFAULT now())"
        )
    elif "intents" not in present:
        # A schema created by an earlier build of version 3.
        statements.append(
            f"ALTER TABLE {subscribers} ADD COLUMN IF NOT EXISTS snapshot_intents "
            f"text[] NOT NULL DEFAULT {_ALL_INTENTS}"
        )
    if "table_index" not in present:
        statements.append(
            "CREATE INDEX IF NOT EXISTS ix_reserved_change_subscribers_table "
            f"ON {subscribers} (table_id)"
        )
    for statement in statements:
        await conn.execute(text(statement))
    if {"changes", "subscribers"} <= present:
        return
    role = query_role_name()
    # The role may not exist (an install whose role could not be created); then
    # there is nothing to revoke from.
    await conn.execute(
        text(
            "DO $$ BEGIN "
            f"IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN "
            f'REVOKE ALL ON {changes} FROM "{role}"; '
            f'REVOKE ALL ON {subscribers} FROM "{role}"; '
            "END IF; END $$"
        )
    )


__all__ = [
    "CHANGES_TABLE",
    "FLOORS_TABLE",
    "PENDING_CHANNEL",
    "PENDING_TABLE",
    "SUBSCRIBERS_TABLE",
    "changes_table",
    "create_change_log",
    "create_global_tables",
    "subscribers_table",
]
