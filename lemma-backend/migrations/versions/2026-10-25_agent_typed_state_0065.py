"""The agent's conversations, runs and messages keep their state in columns.

Every key of their metadata JSON that code decided on -- what opened a
conversation, why a run started, where a queued message stands, what a message
is to its run's answer -- gets a typed column, backfilled here from the key it
replaces. The keys stay in the JSON: this is the expand half, and the code
shipping with it reads and writes only the columns. A later release drops the
keys (CONTRIBUTING: expand, then contract).

`agent_conversations`: `origin` (what it is for, from `source` and the keys
whose presence stood for it, in `run_origin`'s precedence), `workflow_run_id`,
`parent_run_id`, `spawn_key`, `reports_to_parent`, `delegations_out`
(`subagents_out` or `pod_asks_out`), `ask`, `call`, `cwd`, `workspace_id`,
`workspace_name`, `repo` and `todos`. `started_by` gets none: every writer
stamped the literal `SCHEDULE`, which `origin` now says.

`agent_runs`: `trigger` (from `source`), `private_note`, `asked_in_lemma`,
`written_in`, `resumed_tool_call_id`, `retried_run_id`, `queued_behind_run_id`,
`queued_leftovers_behind` (a list of run ids, as it always was),
`stop_requested_after_sequence`, `delivered_to_parent_at` (the run's
`finished_at` where `collected_by_parent` was set), `suspended_while_parked`,
`schedule_firing` and `failure`.

`agent_messages`: `queue_state` (one state for the four queue flags),
`steered_into_run_id`, `steer_dispatched_at`, `client_message_id` (unique per
conversation), `private_note`, `private_note_in_dm`, `written_in`, `answer`
(`is_final_answer`, `is_intermediate_assistant_message`,
`answer_from_last_words`), `held_for_a_person`, `folded_from`,
`synthetic_tool_return` and `parked_in_run_id` (the call's own run, where
`parked_in_run` was true).

A value that does not read as its column's type -- an unknown `source`, an id
that is not a UUID, a timestamp that is not one -- is left out (a default
`origin` of `CHAT`, a `trigger` of `MESSAGE`) and counted; the counts are
logged. Two messages one conversation stored under the same
`client_message_id`, which the new unique index forbids, keep it on the earlier
and drop it from the later one's column, also counted. Nothing a person relies
on is narrowed: every row keeps its JSON.

Adding a column with a constant default rewrites no row on Postgres 11+; the
backfills touch only rows whose JSON carries one of the keys.

The downgrade drops the columns and indexes. The JSON was never changed, so
the code before this reads exactly what it wrote.

An open pull request also claims 0065; whichever merges second is renumbered.

Revision ID: 0065_agent_typed_state
Revises: 0064_grants_every_install
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0065_agent_typed_state"
down_revision = "0064_grants_every_install"
branch_labels = None
depends_on = None

_UUID = (
    "'^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$'"
)
_TIMESTAMP = r"'^\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}'"


def _log():
    import logging

    return logging.getLogger("alembic.runtime.migration")


def _text(column: str, key: str) -> str:
    """The key's value when it is a non-empty JSON string."""
    return (
        f"CASE WHEN jsonb_typeof({column}->'{key}') = 'string' "
        f"THEN nullif({column}->>'{key}', '') END"
    )


def _uuid(column: str, key: str) -> str:
    return f"CASE WHEN {column}->>'{key}' ~ {_UUID} THEN ({column}->>'{key}')::uuid END"


def _flag(column: str, key: str) -> str:
    return f"coalesce({column}->>'{key}' = 'true', false)"


def _object(column: str, key: str) -> str:
    return f"CASE WHEN jsonb_typeof({column}->'{key}') = 'object' THEN {column}->'{key}' END"


def _written_in(column: str) -> str:
    return (
        f"CASE {column}->>'written_in_lemma' WHEN 'group' THEN 'GROUP' "
        f"WHEN 'outsiders' THEN 'OUTSIDERS' END"
    )


def _unreadable(column: str, key: str, readable: str) -> str:
    """Rows holding ``key`` whose value the backfill could not read."""
    return f"({column} ? '{key}' AND jsonb_typeof({column}->'{key}') <> 'null' AND ({readable}) IS NULL)"


# -- agent_conversations ------------------------------------------------------

_M = "conversation_metadata"

#: `run_origin`'s precedence, then `surface_of`'s: who is waiting on it first.
#: NULL for a `source` nothing here knows.
_ORIGIN = f"""
CASE
  WHEN {_M}->>'workflow_run_id' ~ {_UUID}
    OR {_M}->>'source' = 'WORKFLOW_RUN' THEN 'WORKFLOW_STEP'
  WHEN jsonb_typeof({_M}->'ask') = 'object' OR {_M}->>'source' = 'pod_ask'
    THEN 'POD_ASK'
  WHEN {_M}->>'is_sub_agent' = 'true' OR {_M}->'call'->>'role' = 'worker'
    THEN 'SUBAGENT'
  WHEN {_M}->'call'->>'role' = 'coordinator' THEN 'CALL'
  WHEN upper({_M}->>'source') = 'SCHEDULE'
    OR upper({_M}->>'started_by') = 'SCHEDULE' THEN 'SCHEDULE'
  WHEN {_M}->>'source' = 'notification' THEN 'NOTIFICATION'
  WHEN {_M}->>'source' = 'web_widget' OR {_M} ? 'web_widget_id'
    OR contact_id IS NOT NULL THEN 'CONTACT'
  WHEN {_M}->>'source' = 'lemma_channel' THEN 'CHANNEL'
  WHEN {_M}->>'source' = 'agent_surfaces'
    OR coalesce({_M}->>'surface_platform', '') <> ''
    OR coalesce({_M}->>'surface_id', '') <> '' THEN 'SURFACE'
  WHEN conversation_type = 'PROJECT' THEN 'PROJECT'
  WHEN channel_id IS NOT NULL THEN 'CHANNEL'
  WHEN {_M}->>'source' IS NULL THEN 'CHAT'
END
"""

_CWD = f"coalesce(nullif({_M}->'workspace'->>'cwd', ''), {_text(_M, 'cwd')})"
_WORKSPACE_ID = (
    f"coalesce(nullif({_M}->'workspace'->>'id', ''), {_text(_M, 'workspace_id')})"
)
_REPO = (
    f"CASE WHEN jsonb_typeof({_M}->'workspace'->'repo') = 'object' "
    f"THEN {_M}->'workspace'->'repo' "
    f"WHEN jsonb_typeof({_M}->'repo') = 'object' THEN {_M}->'repo' END"
)

_CONVERSATION_BACKFILL = f"""
UPDATE agent_conversations SET
  origin = coalesce({_ORIGIN}, 'CHAT'),
  workflow_run_id = {_uuid(_M, "workflow_run_id")},
  parent_run_id = {_uuid(_M, "parent_run_id")},
  spawn_key = {_text(_M, "spawn_key")},
  reports_to_parent = {_flag(_M, "reports_to_parent")},
  delegations_out = {_flag(_M, "subagents_out")} OR {_flag(_M, "pod_asks_out")},
  ask = {_object(_M, "ask")},
  call = {_object(_M, "call")},
  cwd = {_CWD},
  workspace_id = {_WORKSPACE_ID},
  workspace_name = {_text(_M, "workspace_name")},
  repo = {_REPO},
  todos = CASE WHEN jsonb_typeof({_M}->'todos') = 'array' THEN {_M}->'todos'
    ELSE '[]'::jsonb END
WHERE ({_M} IS NOT NULL AND {_M} <> '{{}}'::jsonb)
   OR contact_id IS NOT NULL OR channel_id IS NOT NULL
   OR conversation_type = 'PROJECT'
"""

_CONVERSATION_UNMAPPED = {
    "source": f"({_ORIGIN}) IS NULL",
    "workflow_run_id": _unreadable(_M, "workflow_run_id", _uuid(_M, "workflow_run_id")),
    "parent_run_id": _unreadable(_M, "parent_run_id", _uuid(_M, "parent_run_id")),
    "ask": _unreadable(_M, "ask", _object(_M, "ask")),
    "call": _unreadable(_M, "call", _object(_M, "call")),
    "repo": _unreadable(_M, "repo", _REPO),
    "todos": f"({_M} ? 'todos' AND jsonb_typeof({_M}->'todos') NOT IN ('array', 'null'))",
}

# -- agent_runs ---------------------------------------------------------------

_R = "run_metadata"

_TRIGGER = f"""
CASE
  WHEN {_R}->>'source' = 'user_message' THEN 'MESSAGE'
  WHEN {_R}->>'source' = 'queued_messages' THEN 'FOLLOW_UP'
  WHEN {_R}->>'source' IN ('approval_resume', 'turn_resume') THEN 'RESUME'
  WHEN {_R}->>'source' IN ('wait_resume', 'agent_wait') THEN 'WAKE'
  WHEN {_R}->>'source' = 'manual_retry' THEN 'RETRY'
  WHEN upper({_R}->>'source') = 'SCHEDULE' THEN 'SCHEDULE_FIRE'
  WHEN {_R}->>'source' = 'WORKFLOW_RUN' THEN 'WORKFLOW_START'
  WHEN {_R}->>'source' = 'message_replies' THEN 'REPLIES'
  WHEN {_R}->>'source' IN ('subagent_result', 'pod_ask_answer')
    THEN 'DELEGATION_RESULT'
  WHEN {_R}->>'source' = 'notification' THEN 'NOTIFICATION'
  WHEN {_R}->>'source' IS NULL THEN 'MESSAGE'
END
"""

_SCHEDULE_FIRING = f"""
CASE WHEN {_R} ?| array['schedule_id', 'schedule_name', 'trigger_type',
  'schedule_type', 'fired_at', 'scheduled_for', 'timezone']
THEN nullif(jsonb_strip_nulls(jsonb_build_object(
  'schedule_id', {_text(_R, "schedule_id")},
  'schedule_name', {_text(_R, "schedule_name")},
  'trigger_type', coalesce({_text(_R, "trigger_type")}, {_text(_R, "schedule_type")}),
  'fired_at', {_text(_R, "fired_at")},
  'scheduled_for', {_text(_R, "scheduled_for")},
  'timezone', {_text(_R, "timezone")}
)), '{{}}'::jsonb) END
"""

_STOP_AFTER = (
    f"CASE WHEN jsonb_typeof({_R}->'stop_requested_after_sequence') = 'number' "
    f"THEN ({_R}->>'stop_requested_after_sequence')::numeric::bigint END"
)

_RUN_BACKFILL = f"""
UPDATE agent_runs SET
  trigger = coalesce({_TRIGGER}, 'MESSAGE'),
  private_note = {_flag(_R, "private_note")},
  asked_in_lemma = {_flag(_R, "asked_in_lemma")},
  written_in = {_written_in(_R)},
  resumed_tool_call_id = {_text(_R, "resumed_tool_call_id")},
  retried_run_id = {_uuid(_R, "retried_agent_run_id")},
  queued_behind_run_id = {_uuid(_R, "queued_behind_agent_run_id")},
  queued_leftovers_behind = CASE
    WHEN jsonb_typeof({_R}->'queued_leftovers_behind') = 'array'
    THEN {_R}->'queued_leftovers_behind' ELSE '[]'::jsonb END,
  stop_requested_after_sequence = {_STOP_AFTER},
  delivered_to_parent_at = CASE WHEN {_R} ? 'collected_by_parent'
    THEN coalesce(finished_at, updated_at) END,
  suspended_while_parked = {_object(_R, "suspended_while_parked")},
  schedule_firing = {_SCHEDULE_FIRING},
  failure = {_object(_R, "failure")}
WHERE {_R} IS NOT NULL AND {_R} <> '{{}}'::jsonb
"""

_RUN_UNMAPPED = {
    "source": f"({_TRIGGER}) IS NULL",
    "written_in_lemma": _unreadable(_R, "written_in_lemma", _written_in(_R)),
    "retried_agent_run_id": _unreadable(
        _R, "retried_agent_run_id", _uuid(_R, "retried_agent_run_id")
    ),
    "queued_behind_agent_run_id": _unreadable(
        _R, "queued_behind_agent_run_id", _uuid(_R, "queued_behind_agent_run_id")
    ),
    "stop_requested_after_sequence": _unreadable(
        _R, "stop_requested_after_sequence", _STOP_AFTER
    ),
    "suspended_while_parked": _unreadable(
        _R, "suspended_while_parked", _object(_R, "suspended_while_parked")
    ),
    "failure": _unreadable(_R, "failure", _object(_R, "failure")),
}

# -- agent_messages -----------------------------------------------------------

_MM = "message_metadata"

#: The queue predicate's own order: a delivered message is steered whatever
#: else it carries; one still owed an answer says why it is still waiting.
_QUEUE_STATE = f"""
CASE
  WHEN {_MM}->>'steered_into_run' IS NOT NULL THEN 'STEERED'
  WHEN {_MM}->>'during_active_run' = 'true'
    AND {_MM}->>'waiting_on_answer' IS NOT NULL THEN 'WAITING_ON_ANSWER'
  WHEN {_MM}->>'during_active_run' = 'true'
    AND {_MM}->>'steer_undelivered' IS NOT NULL THEN 'STEER_UNDELIVERED'
  WHEN {_MM}->>'during_active_run' = 'true' THEN 'QUEUED'
  ELSE 'NONE'
END
"""

_ANSWER = f"""
CASE
  WHEN {_MM}->>'is_final_answer' = 'true'
    AND {_MM}->>'answer_from_last_words' = 'true' THEN 'LAST_WORDS'
  WHEN {_MM}->>'is_final_answer' = 'true' THEN 'FINAL'
  WHEN {_MM}->>'is_intermediate_assistant_message' = 'true' THEN 'INTERMEDIATE'
  WHEN {_MM}->>'is_final_answer' = 'false' THEN 'NOT_FINAL'
END
"""

_STEER_DISPATCHED_AT = (
    f"CASE WHEN {_MM}->>'steer_dispatched_at' ~ {_TIMESTAMP} "
    f"THEN ({_MM}->>'steer_dispatched_at')::timestamptz END"
)

_MESSAGE_KEYS = (
    "during_active_run",
    "steered_into_run",
    "steer_dispatched_at",
    "steer_undelivered",
    "waiting_on_answer",
    "client_message_id",
    "private_note",
    "private_note_in_dm",
    "written_in_lemma",
    "is_final_answer",
    "is_intermediate_assistant_message",
    "answer_from_last_words",
    "held_for_a_person",
    "folded_from",
    "synthetic_tool_return",
    "parked_in_run",
)
_HOLDS_A_MESSAGE_KEY = (
    f"{_MM} ?| array[{', '.join(repr(key) for key in _MESSAGE_KEYS)}]"
)

_MESSAGE_BACKFILL = f"""
UPDATE agent_messages SET
  queue_state = {_QUEUE_STATE},
  steered_into_run_id = {_uuid(_MM, "steered_into_run")},
  steer_dispatched_at = {_STEER_DISPATCHED_AT},
  client_message_id = {_text(_MM, "client_message_id")},
  private_note = {_flag(_MM, "private_note")},
  private_note_in_dm = {_flag(_MM, "private_note_in_dm")},
  written_in = {_written_in(_MM)},
  answer = {_ANSWER},
  held_for_a_person = {_flag(_MM, "held_for_a_person")},
  folded_from = {_uuid(_MM, "folded_from")},
  synthetic_tool_return = {_flag(_MM, "synthetic_tool_return")},
  parked_in_run_id = CASE WHEN {_MM}->>'parked_in_run' = 'true'
    THEN agent_run_id END
WHERE {_HOLDS_A_MESSAGE_KEY}
"""

_MESSAGE_UNMAPPED = {
    "steered_into_run": _unreadable(
        _MM, "steered_into_run", _uuid(_MM, "steered_into_run")
    ),
    "steer_dispatched_at": _unreadable(
        _MM, "steer_dispatched_at", _STEER_DISPATCHED_AT
    ),
    "client_message_id": _unreadable(
        _MM, "client_message_id", _text(_MM, "client_message_id")
    ),
    "written_in_lemma": _unreadable(_MM, "written_in_lemma", _written_in(_MM)),
    "folded_from": _unreadable(_MM, "folded_from", _uuid(_MM, "folded_from")),
    "parked_in_run": (f"({_MM}->>'parked_in_run' = 'true' AND agent_run_id IS NULL)"),
}

#: The later of two messages one conversation stored under the same send id.
_CLIENT_ID_DUPLICATES = """
UPDATE agent_messages SET client_message_id = NULL
WHERE id IN (
  SELECT id FROM (
    SELECT id, row_number() OVER (
      PARTITION BY conversation_id, client_message_id ORDER BY sequence, id
    ) AS copy
    FROM agent_messages WHERE client_message_id IS NOT NULL
  ) ranked WHERE copy > 1
)
"""


def _enum(name: str, *values: str, length: int) -> sa.Enum:
    return sa.Enum(*values, name=name, native_enum=False, length=length)


def _flag_column(name: str) -> sa.Column:
    return sa.Column(name, sa.Boolean(), nullable=False, server_default=sa.false())


_WRITTEN_IN = ("GROUP", "OUTSIDERS")


def _add_columns() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    for column in (
        sa.Column(
            "origin",
            _enum(
                "conversationorigin",
                "CHAT",
                "PROJECT",
                "SURFACE",
                "CHANNEL",
                "CONTACT",
                "WORKFLOW_STEP",
                "SCHEDULE",
                "SUBAGENT",
                "POD_ASK",
                "CALL",
                "NOTIFICATION",
                "SETUP",
                length=32,
            ),
            nullable=False,
            server_default="CHAT",
        ),
        sa.Column("workflow_run_id", sa.Uuid(), nullable=True),
        sa.Column("parent_run_id", sa.Uuid(), nullable=True),
        sa.Column("spawn_key", sa.Text(), nullable=True),
        _flag_column("reports_to_parent"),
        _flag_column("delegations_out"),
        sa.Column("ask", jsonb, nullable=True),
        sa.Column("call", jsonb, nullable=True),
        sa.Column("cwd", sa.Text(), nullable=True),
        sa.Column("workspace_id", sa.Text(), nullable=True),
        sa.Column("workspace_name", sa.Text(), nullable=True),
        sa.Column("repo", jsonb, nullable=True),
        sa.Column(
            "todos", jsonb, nullable=False, server_default=sa.text("'[]'::jsonb")
        ),
    ):
        op.add_column("agent_conversations", column)

    for column in (
        sa.Column(
            "trigger",
            _enum(
                "runtrigger",
                "MESSAGE",
                "FOLLOW_UP",
                "RESUME",
                "WAKE",
                "RETRY",
                "REPLIES",
                "DELEGATION_RESULT",
                "SCHEDULE_FIRE",
                "WORKFLOW_START",
                "NOTIFICATION",
                length=32,
            ),
            nullable=False,
            server_default="MESSAGE",
        ),
        _flag_column("private_note"),
        _flag_column("asked_in_lemma"),
        sa.Column(
            "written_in", _enum("writtenin", *_WRITTEN_IN, length=16), nullable=True
        ),
        sa.Column("resumed_tool_call_id", sa.Text(), nullable=True),
        sa.Column("retried_run_id", sa.Uuid(), nullable=True),
        sa.Column("queued_behind_run_id", sa.Uuid(), nullable=True),
        sa.Column(
            "queued_leftovers_behind",
            jsonb,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("stop_requested_after_sequence", sa.BigInteger(), nullable=True),
        sa.Column("delivered_to_parent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("suspended_while_parked", jsonb, nullable=True),
        sa.Column("schedule_firing", jsonb, nullable=True),
        sa.Column("failure", jsonb, nullable=True),
    ):
        op.add_column("agent_runs", column)

    for column in (
        sa.Column(
            "queue_state",
            _enum(
                "queuestate",
                "NONE",
                "QUEUED",
                "STEERED",
                "STEER_UNDELIVERED",
                "WAITING_ON_ANSWER",
                length=32,
            ),
            nullable=False,
            server_default="NONE",
        ),
        sa.Column("steered_into_run_id", sa.Uuid(), nullable=True),
        sa.Column("steer_dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("client_message_id", sa.Text(), nullable=True),
        _flag_column("private_note"),
        _flag_column("private_note_in_dm"),
        sa.Column(
            "written_in", _enum("writtenin", *_WRITTEN_IN, length=16), nullable=True
        ),
        sa.Column(
            "answer",
            _enum(
                "answerkind",
                "FINAL",
                "INTERMEDIATE",
                "LAST_WORDS",
                "NOT_FINAL",
                length=16,
            ),
            nullable=True,
        ),
        _flag_column("held_for_a_person"),
        sa.Column("folded_from", sa.Uuid(), nullable=True),
        _flag_column("synthetic_tool_return"),
        sa.Column("parked_in_run_id", sa.Uuid(), nullable=True),
    ):
        op.add_column("agent_messages", column)


def _report(conn, table: str, unmapped: dict[str, str]) -> None:
    """Log, per key, how many rows hold a value the backfill cannot read.

    One scan of the table for every key at once.
    """
    counts = ", ".join(
        f"count(*) FILTER (WHERE {where})" for where in unmapped.values()
    )
    row = conn.execute(sa.text(f"SELECT {counts} FROM {table}")).one()
    for key, found in zip(unmapped, row, strict=True):
        if found:
            _log().warning(
                "0065: %d %s rows hold a %r the backfill could not read; "
                "left at the column's default.",
                found,
                table,
                key,
            )
        else:
            _log().info("0065: every %s %r read.", table, key)


def upgrade() -> None:
    conn = op.get_bind()
    _add_columns()

    # Counted before the backfill, against the JSON it reads.
    _report(conn, "agent_conversations", _CONVERSATION_UNMAPPED)
    _report(conn, "agent_runs", _RUN_UNMAPPED)
    _report(conn, "agent_messages", _MESSAGE_UNMAPPED)

    conversations = conn.execute(sa.text(_CONVERSATION_BACKFILL)).rowcount
    runs = conn.execute(sa.text(_RUN_BACKFILL)).rowcount
    messages = conn.execute(sa.text(_MESSAGE_BACKFILL)).rowcount
    send_copies = conn.execute(sa.text(_CLIENT_ID_DUPLICATES)).rowcount
    _log().info(
        "0065: backfilled %d conversations, %d runs, %d messages.",
        conversations,
        runs,
        messages,
    )
    _log().info(
        "0065: %d later client_message_id copies dropped from the column; "
        "the JSON keeps them.",
        send_copies,
    )

    op.create_index(
        "ix_agent_conv_spawn_key",
        "agent_conversations",
        ["parent_id", "spawn_key"],
        postgresql_where=sa.text("spawn_key IS NOT NULL"),
    )
    op.create_index(
        "ix_agent_conv_ask",
        "agent_conversations",
        ["ask"],
        postgresql_using="gin",
        postgresql_where=sa.text("ask IS NOT NULL"),
    )
    op.create_index(
        "uq_agent_message_client_id",
        "agent_messages",
        ["conversation_id", "client_message_id"],
        unique=True,
        postgresql_where=sa.text("client_message_id IS NOT NULL"),
    )
    op.create_index(
        "ix_agent_message_queued",
        "agent_messages",
        ["conversation_id", "queue_state"],
        postgresql_where=sa.text("queue_state <> 'NONE'"),
    )


def downgrade() -> None:
    op.drop_index("ix_agent_message_queued", table_name="agent_messages")
    op.drop_index("uq_agent_message_client_id", table_name="agent_messages")
    op.drop_index("ix_agent_conv_ask", table_name="agent_conversations")
    op.drop_index("ix_agent_conv_spawn_key", table_name="agent_conversations")
    for table, columns in (
        (
            "agent_messages",
            (
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
            ),
        ),
        (
            "agent_runs",
            (
                "trigger",
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
            ),
        ),
        (
            "agent_conversations",
            (
                "origin",
                "workflow_run_id",
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
            ),
        ),
    ):
        for column in columns:
            op.drop_column(table, column)
