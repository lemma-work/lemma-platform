"""The agent's conversations, runs and messages keep their state in columns.

Every key of their metadata JSON that code decided on -- what opened a
conversation, why a run started, where a queued message stands, what a message
is to its run's answer -- gets a typed column, backfilled here from the key it
replaces. The keys stay in the JSON: this is the expand half, and the code
shipping with it reads and writes only the columns. A later release drops the
keys (CONTRIBUTING: expand, then contract).

`agent_conversations`: `origin` (what it is for, from `source` and the keys
whose presence stood for it, in `run_origin`'s precedence), `workflow_run_id`,
`schedule_type` (what fires a schedule's conversation), `parent_run_id`, `spawn_key`, `reports_to_parent`, `delegations_out`
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

`agent_pauses` is new: one row per pausing tool call, the `Pause` aggregate.
It is backfilled from `agent_conversation_waits` (same ids, so a queued
`wait:<id>` timer still finds its wait), from `agent_approval_decisions` (each
joined to its call for the kind; a decision recorded on a `wait_for` call is
the wait's, already covered), and from every pausing call still open -- no
return, no decision. The kind of a `request_approval` call comes from the
markers in its arguments (`run_budget_pause`, `agent_host_permission`), which
become the typed `budget` and `host_permission`. Rows that cannot be mapped --
an unknown status, a call that is gone, a run that is gone, an open `wait_for`
with no wait -- are counted and logged. The two old tables stay, unwritten, for
one release; the downgrade copies what the new table holds back into them.

The Agent Host gets two tables of its own. `agent_host_sessions`, one row per
conversation, holds what the conversation's `agent_host_provider_session_id`
key held: the provider session, its harness and host, the run that bound it
and when that run began, the host's working directory, the instructions digest
delivered, the instructions and prompt still pending (each with its run), and
the newest prompted run with how far into the conversation its prompt ran.
`agent_host_run_records`, one row per run, holds the run's `execution`,
`host_native_commands` and `final_answer` keys. A binding that is not a JSON
object, and an unreadable value inside one, are counted and logged; the
downgrade writes both tables back into the keys, so the code before this reads
what the code after it wrote.

agent_surfaces gets `surface_conversation_state`, one row per conversation a
chat surface opened or heard: the place it was last written to from
(`surface_id`, `surface_platform`, `conversation_kind`, the platform's
`external_*` ids, `channel_name`, `route_key`, `routed_agent_id`,
`agent_display_name`, `surface_event_metadata`), a web widget's visitor
(`web_widget_id`, `web_session_id`), and the two records agent_surfaces kept
in the conversation's metadata (`surface_held_replies` as `held_replies`,
`surface_free_text_answer_for` as `free_text_answer_for`). Backfilled from
those keys; a value that does not read as its column is counted and logged.
The downgrade writes the table back into the keys, and `schedule_type` back
into its key.

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

An open pull request (#1046, `outcome_weeks`) also claims 0066; whichever
merges second is renumbered.

Revision ID: 0066_agent_typed_state
Revises: 0065_schedule_reset_and_author
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0066_agent_typed_state"
down_revision = "0065_schedule_reset_and_author"
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

#: What fires a schedule's conversation, as ``schedule.ScheduleType`` names it.
_SCHEDULE_TYPE = (
    f"CASE WHEN upper({_M}->>'schedule_type') IN ('TIME', 'DATASTORE', 'WEBHOOK') "
    f"THEN upper({_M}->>'schedule_type') END"
)

_CONVERSATION_BACKFILL = f"""
UPDATE agent_conversations SET
  origin = coalesce({_ORIGIN}, 'CHAT'),
  workflow_run_id = {_uuid(_M, "workflow_run_id")},
  schedule_type = {_SCHEDULE_TYPE},
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
    "schedule_type": _unreadable(_M, "schedule_type", _SCHEDULE_TYPE),
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
        sa.Column("schedule_type", sa.String(16), nullable=True),
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
                "0066: %d %s rows hold a %r the backfill could not read; "
                "left at the column's default.",
                found,
                table,
                key,
            )
        else:
            _log().info("0066: every %s %r read.", table, key)


# -- agent_pauses ---------------------------------------------------------------

#: One stored call per (conversation, call id), the first written.
_CALLS = """
calls AS (
  SELECT DISTINCT ON (conversation_id, tool_call_id) *
  FROM agent_messages
  WHERE kind = 'TOOL_CALL' AND tool_call_id IS NOT NULL
    AND tool_name IN ('ask_user', 'request_approval', 'browser_sign_in')
  ORDER BY conversation_id, tool_call_id, sequence
)
"""

_KIND = """
CASE
  WHEN m.tool_name = 'ask_user' THEN 'QUESTION'
  WHEN m.tool_name = 'browser_sign_in' THEN 'SIGN_IN'
  WHEN jsonb_typeof(m.tool_args->'run_budget_pause') = 'object' THEN 'BUDGET'
  WHEN jsonb_typeof(m.tool_args->'agent_host_permission') = 'object'
    AND m.tool_args->'agent_host_permission'->>'request_id' <> ''
    THEN 'HOST_PERMISSION'
  ELSE 'APPROVAL'
END
"""

_PAUSE_COLUMNS = (
    "id, created_at, updated_at, conversation_id, run_id, pod_id, tool_call_id, "
    "tool_name, kind, status, deadline, resolution, host_permission, budget, "
    "execution_claimed_at, resolved_at"
)


def _from_calls(status: str, resolution: str, claimed: str, resolved: str) -> str:
    """The SELECT list of a pause opened by call ``m`` (its kind as ``k.kind``)."""
    return f"""
  m.created_at, m.created_at, m.conversation_id, m.agent_run_id, c.pod_id,
  m.tool_call_id, m.tool_name, k.kind, {status},
  CASE WHEN k.kind = 'QUESTION' THEN m.created_at + interval '72 hours'
       WHEN k.kind IN ('APPROVAL', 'BUDGET', 'HOST_PERMISSION')
       THEN m.created_at + interval '7 days' END,
  {resolution},
  CASE WHEN k.kind = 'HOST_PERMISSION' THEN jsonb_build_object(
    'request_id', m.tool_args->'agent_host_permission'->>'request_id',
    'options', CASE WHEN jsonb_typeof(m.tool_args->'agent_host_permission'->'options')
      = 'array' THEN m.tool_args->'agent_host_permission'->'options' ELSE '[]' END,
    'input', CASE WHEN jsonb_typeof(m.tool_args->'agent_host_permission'->'input')
      = 'object' THEN m.tool_args->'agent_host_permission'->'input' ELSE '{{}}' END,
    'tool_name', m.tool_args->>'tool_name') END,
  CASE WHEN k.kind = 'BUDGET' THEN m.tool_args->'run_budget_pause' END,
  {claimed}, {resolved}
"""


_WAKE_REASONS = (
    "'TIMER', 'CANCELLED', 'ANSWERED', 'TARGET_FINISHED', 'TARGET_GONE', "
    "'DEADLINE', 'TARGET_NEEDS_INPUT', 'TARGET_UNREADABLE'"
)

_WAITS = f"""
INSERT INTO agent_pauses ({_PAUSE_COLUMNS}, wait_type, external_ref, wake_at,
  wait_spec)
SELECT w.id, w.created_at, w.updated_at, w.conversation_id, w.agent_run_id,
  w.pod_id, w.tool_call_id, 'wait_for', 'WAIT',
  CASE w.status WHEN 'ACTIVE' THEN 'PENDING' WHEN 'COMPLETED' THEN 'RESOLVED'
    WHEN 'CANCELLED' THEN 'CANCELLED' WHEN 'FAILED' THEN 'ABANDONED' END,
  NULL,
  CASE WHEN w.spec->>'woke_because' IN ({_WAKE_REASONS})
    THEN jsonb_build_object('woke_because', w.spec->>'woke_because') END,
  NULL, NULL, NULL, w.completed_at,
  w.wait_type, w.external_ref, w.scheduled_at, coalesce(w.spec, '{{}}')
FROM agent_conversation_waits w
WHERE w.status IN ('ACTIVE', 'COMPLETED', 'CANCELLED', 'FAILED')
  AND w.wait_type IN ('TIME', 'PROCESS', 'SUBAGENT')
ON CONFLICT (conversation_id, tool_call_id) DO NOTHING
"""

_DECIDED = _from_calls(
    status="CASE WHEN d.response->>'superseded_by_new_message' = 'true' "
    "THEN 'SUPERSEDED' WHEN d.response->>'expired_unanswered' = 'true' "
    "THEN 'EXPIRED' ELSE 'RESOLVED' END",
    resolution="jsonb_build_object('decision', d.decision, 'response', "
    "coalesce(d.response, '{}'), 'resolved_by_user_id', d.resolved_by_user_id)",
    claimed="d.execution_claimed_at",
    resolved="d.created_at",
)
_UNDECIDED = _from_calls(
    status="'PENDING'", resolution="NULL", claimed="NULL", resolved="NULL"
)

_DECISIONS = f"""
WITH {_CALLS}
INSERT INTO agent_pauses ({_PAUSE_COLUMNS})
SELECT d.id, {_DECIDED}
FROM agent_approval_decisions d
JOIN calls m ON m.conversation_id = d.conversation_id
  AND m.tool_call_id = d.approval_id
JOIN agent_conversations c ON c.id = d.conversation_id
CROSS JOIN LATERAL (SELECT {_KIND} AS kind) k
WHERE d.decision IN ('APPROVE_ONCE', 'APPROVE_FOR_SESSION', 'DENY')
  AND EXISTS (SELECT 1 FROM agent_runs r WHERE r.id = m.agent_run_id)
ON CONFLICT (conversation_id, tool_call_id) DO NOTHING
"""

_OPEN_CALLS = f"""
WITH {_CALLS}
INSERT INTO agent_pauses ({_PAUSE_COLUMNS})
SELECT gen_random_uuid(), {_UNDECIDED}
FROM calls m
JOIN agent_conversations c ON c.id = m.conversation_id
CROSS JOIN LATERAL (SELECT {_KIND} AS kind) k
WHERE EXISTS (SELECT 1 FROM agent_runs r WHERE r.id = m.agent_run_id)
  AND NOT EXISTS (
    SELECT 1 FROM agent_messages r WHERE r.conversation_id = m.conversation_id
      AND r.tool_call_id = m.tool_call_id AND r.kind = 'TOOL_RETURN')
  AND NOT EXISTS (
    SELECT 1 FROM agent_approval_decisions d
    WHERE d.conversation_id = m.conversation_id AND d.approval_id = m.tool_call_id)
ON CONFLICT (conversation_id, tool_call_id) DO NOTHING
"""

#: What the backfill could not carry over, counted after it ran.
_PAUSES_UNMAPPED = {
    "waits": "SELECT count(*) FROM agent_conversation_waits w WHERE NOT EXISTS ("
    "SELECT 1 FROM agent_pauses p WHERE p.id = w.id)",
    "approval decisions": """
      SELECT count(*) FROM agent_approval_decisions d
      WHERE NOT EXISTS (SELECT 1 FROM agent_pauses p
        WHERE p.conversation_id = d.conversation_id AND p.tool_call_id = d.approval_id)
      AND NOT EXISTS (SELECT 1 FROM agent_messages m
        WHERE m.conversation_id = d.conversation_id
          AND m.tool_call_id = d.approval_id AND m.tool_name = 'wait_for')
    """,
    "open pausing calls": """
      SELECT count(*) FROM agent_messages m
      WHERE m.kind = 'TOOL_CALL' AND m.tool_call_id IS NOT NULL
        AND m.tool_name IN ('ask_user', 'request_approval', 'browser_sign_in',
          'wait_for')
        AND NOT EXISTS (SELECT 1 FROM agent_messages r
          WHERE r.conversation_id = m.conversation_id
            AND r.tool_call_id = m.tool_call_id AND r.kind = 'TOOL_RETURN')
        AND NOT EXISTS (SELECT 1 FROM agent_pauses p
          WHERE p.conversation_id = m.conversation_id
            AND p.tool_call_id = m.tool_call_id)
    """,
}


def _create_pauses() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "agent_pauses",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("tool_call_id", sa.String(255), nullable=False),
        sa.Column("tool_name", sa.String(255), nullable=False),
        sa.Column(
            "kind",
            _enum(
                "pausekind",
                "QUESTION",
                "APPROVAL",
                "SIGN_IN",
                "BUDGET",
                "HOST_PERMISSION",
                "WAIT",
                length=32,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            _enum(
                "pausestatus",
                "PENDING",
                "RESOLVED",
                "EXPIRED",
                "SUPERSEDED",
                "CANCELLED",
                "ABANDONED",
                length=32,
            ),
            nullable=False,
            server_default="PENDING",
        ),
        sa.Column("deadline", timestamp, nullable=True),
        sa.Column("resolution", jsonb, nullable=True),
        sa.Column("host_permission", jsonb, nullable=True),
        sa.Column("budget", jsonb, nullable=True),
        sa.Column(
            "wait_type",
            _enum("agentwaittype", "TIME", "PROCESS", "SUBAGENT", length=30),
            nullable=True,
        ),
        sa.Column("external_ref", sa.String(), nullable=True),
        sa.Column("wake_at", timestamp, nullable=True),
        sa.Column("wait_spec", jsonb, nullable=True),
        sa.Column("execution_claimed_at", timestamp, nullable=True),
        sa.Column("resolved_at", timestamp, nullable=True),
        sa.UniqueConstraint(
            "conversation_id", "tool_call_id", name="uq_agent_pause_tool_call"
        ),
    )
    op.create_index("ix_agent_pauses_run_id", "agent_pauses", ["run_id"])
    op.create_index(
        "ix_agent_pause_conversation_status",
        "agent_pauses",
        ["conversation_id", "status"],
    )
    op.create_index(
        "uq_agent_pause_one_pending_wait",
        "agent_pauses",
        ["conversation_id"],
        unique=True,
        postgresql_where=sa.text("kind = 'WAIT' AND status = 'PENDING'"),
    )


def _backfill_pauses(conn) -> None:
    waits = conn.execute(sa.text(_WAITS)).rowcount
    decisions = conn.execute(sa.text(_DECISIONS)).rowcount
    open_calls = conn.execute(sa.text(_OPEN_CALLS)).rowcount
    _log().info(
        "0066: agent_pauses backfilled from %d waits, %d approval decisions and "
        "%d open pausing calls.",
        waits,
        decisions,
        open_calls,
    )
    for source, count_sql in _PAUSES_UNMAPPED.items():
        found = conn.execute(sa.text(count_sql)).scalar_one()
        if found:
            _log().warning(
                "0066: %d %s could not be carried into agent_pauses; the old "
                "table keeps them.",
                found,
                source,
            )
        else:
            _log().info("0066: every one of the %s carried into agent_pauses.", source)


#: The downgrade's way back: what the old code reads, from what the new wrote.
_RESTORE_WAITS = """
INSERT INTO agent_conversation_waits (id, created_at, updated_at, conversation_id,
  agent_run_id, pod_id, tool_call_id, wait_type, status, external_ref,
  scheduled_at, spec, completed_at)
SELECT id, created_at, updated_at, conversation_id, run_id, pod_id, tool_call_id,
  wait_type, CASE status WHEN 'PENDING' THEN 'ACTIVE' WHEN 'RESOLVED' THEN 'COMPLETED'
    WHEN 'ABANDONED' THEN 'FAILED' ELSE 'CANCELLED' END,
  external_ref, wake_at, wait_spec, resolved_at
FROM agent_pauses WHERE kind = 'WAIT' AND wait_type IS NOT NULL
ON CONFLICT (id) DO UPDATE SET status = excluded.status, spec = excluded.spec,
  scheduled_at = excluded.scheduled_at, completed_at = excluded.completed_at
"""

_RESTORE_DECISIONS = """
INSERT INTO agent_approval_decisions (id, created_at, conversation_id, agent_run_id,
  approval_id, tool_name, decision, response, resolved_by_user_id,
  execution_claimed_at)
SELECT id, coalesce(resolved_at, updated_at), conversation_id, run_id, tool_call_id,
  CASE WHEN kind = 'QUESTION' THEN 'ask_user' ELSE tool_name END,
  resolution->>'decision', resolution->'response',
  (resolution->>'resolved_by_user_id')::uuid, execution_claimed_at
FROM agent_pauses
WHERE kind <> 'WAIT' AND resolution->>'decision' IS NOT NULL
ON CONFLICT (conversation_id, approval_id) DO NOTHING
"""


# -- agent_host_sessions and agent_host_run_records -------------------------------

_B = "(conversation_metadata->'agent_host_provider_session_id')"
_PI = f"({_B}->'pending_instructions')"
_PP = f"({_B}->'pending_prompt')"


def _number(column: str, key: str) -> str:
    return (
        f"CASE WHEN jsonb_typeof({column}->'{key}') = 'number' "
        f"THEN ({column}->>'{key}')::numeric::bigint END"
    )


_RUN_CREATED_AT = (
    f"CASE WHEN {_B}->>'run_created_at' ~ {_TIMESTAMP} "
    f"THEN ({_B}->>'run_created_at')::timestamptz END"
)

_SESSIONS_BACKFILL = f"""
INSERT INTO agent_host_sessions (conversation_id, session_id, harness_id, host_id,
  run_id, run_created_at, host_cwd, instructions_digest,
  pending_instructions_run_id, pending_instructions_digest, pending_prompt_run_id,
  pending_prompt_seen_through, prompted_run_id, prompted_through, created_at,
  updated_at)
SELECT id, {_text(_B, "session_id")}, {_uuid(_B, "harness_id")},
  {_uuid(_B, "host_id")}, {_uuid(_B, "run_id")}, {_RUN_CREATED_AT},
  {_text(_B, "host_cwd")}, {_text(_B, "instructions_digest")},
  {_uuid(_PI, "run_id")}, {_text(_PI, "digest")}, {_uuid(_PP, "run_id")},
  {_number(_PP, "seen_through")}, {_uuid(_B, "prompted_run_id")},
  {_number(_B, "prompted_through")}, now(), now()
FROM agent_conversations
WHERE jsonb_typeof({_B}) = 'object'
"""

_SESSIONS_UNMAPPED = {
    "agent_host_provider_session_id": (
        f"{_M} ? 'agent_host_provider_session_id' "
        f"AND jsonb_typeof({_B}) NOT IN ('object', 'null')"
    ),
    **{
        f"agent_host_provider_session_id.{key}": _unreadable(_B, key, _uuid(_B, key))
        for key in ("harness_id", "host_id", "run_id", "prompted_run_id")
    },
    "agent_host_provider_session_id.run_created_at": _unreadable(
        _B, "run_created_at", _RUN_CREATED_AT
    ),
}

_RECORD_KEYS = ("execution", "host_native_commands", "final_answer")

_RUN_RECORDS_BACKFILL = f"""
INSERT INTO agent_host_run_records (run_id, execution, host_native_commands,
  final_answer, created_at, updated_at)
SELECT id, {_object(_R, "execution")}, {_object(_R, "host_native_commands")},
  {_object(_R, "final_answer")}, now(), now()
FROM agent_runs
WHERE jsonb_typeof({_R}->'execution') = 'object'
  OR jsonb_typeof({_R}->'host_native_commands') = 'object'
  OR jsonb_typeof({_R}->'final_answer') = 'object'
"""

_RUN_RECORDS_UNMAPPED = {
    key: _unreadable(_R, key, _object(_R, key)) for key in _RECORD_KEYS
}


def _create_agent_host_tables() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    timestamp = sa.DateTime(timezone=True)
    op.create_table(
        "agent_host_sessions",
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("session_id", sa.Text(), nullable=True),
        sa.Column("harness_id", sa.Uuid(), nullable=True),
        sa.Column("host_id", sa.Uuid(), nullable=True),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("run_created_at", timestamp, nullable=True),
        sa.Column("host_cwd", sa.Text(), nullable=True),
        sa.Column("instructions_digest", sa.String(64), nullable=True),
        sa.Column("pending_instructions_run_id", sa.Uuid(), nullable=True),
        sa.Column("pending_instructions_digest", sa.String(64), nullable=True),
        sa.Column("pending_prompt_run_id", sa.Uuid(), nullable=True),
        sa.Column("pending_prompt_seen_through", sa.BigInteger(), nullable=True),
        sa.Column("prompted_run_id", sa.Uuid(), nullable=True),
        sa.Column("prompted_through", sa.BigInteger(), nullable=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
    )
    op.create_table(
        "agent_host_run_records",
        sa.Column(
            "run_id",
            sa.Uuid(),
            sa.ForeignKey("agent_runs.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("execution", jsonb, nullable=True),
        sa.Column("host_native_commands", jsonb, nullable=True),
        sa.Column("final_answer", jsonb, nullable=True),
        sa.Column("created_at", timestamp, nullable=False),
        sa.Column("updated_at", timestamp, nullable=False),
    )


def _backfill_agent_host_tables(conn) -> None:
    _report(conn, "agent_conversations", _SESSIONS_UNMAPPED)
    _report(conn, "agent_runs", _RUN_RECORDS_UNMAPPED)
    sessions = conn.execute(sa.text(_SESSIONS_BACKFILL)).rowcount
    records = conn.execute(sa.text(_RUN_RECORDS_BACKFILL)).rowcount
    _log().info(
        "0066: backfilled %d agent_host_sessions and %d agent_host_run_records.",
        sessions,
        records,
    )


#: The downgrade's way back into the keys the old code reads.
_RESTORE_SESSIONS = """
UPDATE agent_conversations c SET conversation_metadata = jsonb_set(
  coalesce(c.conversation_metadata, '{}'::jsonb),
  '{agent_host_provider_session_id}',
  jsonb_strip_nulls(jsonb_build_object(
    'session_id', s.session_id, 'harness_id', s.harness_id::text,
    'host_id', s.host_id::text, 'run_id', s.run_id::text,
    'run_created_at', s.run_created_at, 'host_cwd', s.host_cwd,
    'instructions_digest', s.instructions_digest,
    'pending_instructions', CASE WHEN s.pending_instructions_run_id IS NOT NULL
      THEN jsonb_build_object('run_id', s.pending_instructions_run_id::text,
        'digest', s.pending_instructions_digest) END,
    'pending_prompt', CASE WHEN s.pending_prompt_run_id IS NOT NULL
      THEN jsonb_build_object('run_id', s.pending_prompt_run_id::text,
        'seen_through', s.pending_prompt_seen_through) END,
    'prompted_run_id', s.prompted_run_id::text,
    'prompted_through', s.prompted_through)))
FROM agent_host_sessions s WHERE s.conversation_id = c.id
"""

_RESTORE_RUN_RECORDS = """
UPDATE agent_runs r SET run_metadata = coalesce(r.run_metadata, '{}'::jsonb)
  || jsonb_strip_nulls(jsonb_build_object('execution', x.execution,
    'host_native_commands', x.host_native_commands,
    'final_answer', x.final_answer))
FROM agent_host_run_records x WHERE x.run_id = r.id
"""


# -- surface_conversation_state (agent_surfaces) ------------------------------

#: ``(column, its key, how the key reads as the column)``.
_SURFACE_COLUMNS = (
    ("surface_id", "surface_id", _uuid(_M, "surface_id")),
    ("surface_platform", "surface_platform", None),
    ("conversation_kind", "conversation_kind", None),
    ("external_channel_id", "external_channel_id", None),
    ("external_thread_id", "external_thread_id", None),
    ("external_user_id", "external_user_id", None),
    ("external_message_id", "external_message_id", None),
    ("channel_name", "channel_name", None),
    ("route_key", "route_key", None),
    ("routed_agent_id", "routed_agent_id", _uuid(_M, "routed_agent_id")),
    ("agent_display_name", "agent_display_name", None),
    (
        "surface_event_metadata",
        "surface_event_metadata",
        _object(_M, "surface_event_metadata"),
    ),
    ("web_widget_id", "web_widget_id", _uuid(_M, "web_widget_id")),
    ("web_session_id", "web_session_id", _uuid(_M, "web_session_id")),
    ("held_replies", "surface_held_replies", _object(_M, "surface_held_replies")),
    ("free_text_answer_for", "surface_free_text_answer_for", None),
)

#: The two short ones are capped by their columns; a longer value is no
#: platform or kind any surface writes.
_SURFACE_LENGTHS = {"surface_platform": 32, "conversation_kind": 16}


def _surface_value(column: str, key: str, readable: str | None) -> str:
    if readable is not None:
        return readable
    value = _text(_M, key)
    limit = _SURFACE_LENGTHS.get(column)
    return f"CASE WHEN length({value}) <= {limit} THEN {value} END" if limit else value


_SURFACE_KEYS_SQL = ", ".join(f"'{key}'" for _, key, _ in _SURFACE_COLUMNS)

_SURFACE_BACKFILL = f"""
INSERT INTO surface_conversation_state (conversation_id,
  {", ".join(column for column, _, _ in _SURFACE_COLUMNS)}, created_at, updated_at)
SELECT id, {", ".join(_surface_value(*entry) for entry in _SURFACE_COLUMNS)},
  now(), now()
FROM agent_conversations
WHERE {_M} ?| array[{_SURFACE_KEYS_SQL}]
"""

_SURFACE_UNMAPPED = {
    key: _unreadable(_M, key, _surface_value(column, key, readable))
    for column, key, readable in _SURFACE_COLUMNS
}

#: The downgrade's way back into the keys the old code reads.
_RESTORE_SURFACES = f"""
UPDATE agent_conversations c SET conversation_metadata =
  coalesce(c.conversation_metadata, '{{}}'::jsonb) || jsonb_strip_nulls(jsonb_build_object(
    {", ".join(f"'{key}', s.{column}" for column, key, _ in _SURFACE_COLUMNS)}))
FROM surface_conversation_state s WHERE s.conversation_id = c.id
"""

_RESTORE_SCHEDULE_TYPES = f"""
UPDATE agent_conversations SET conversation_metadata =
  coalesce({_M}, '{{}}'::jsonb) || jsonb_build_object('schedule_type', schedule_type)
WHERE schedule_type IS NOT NULL
"""


def _create_surface_table() -> None:
    jsonb = postgresql.JSONB(astext_type=sa.Text())
    op.create_table(
        "surface_conversation_state",
        sa.Column(
            "conversation_id",
            sa.Uuid(),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("surface_id", sa.Uuid(), nullable=True),
        sa.Column("surface_platform", sa.String(32), nullable=True),
        sa.Column("conversation_kind", sa.String(16), nullable=True),
        sa.Column("external_channel_id", sa.Text(), nullable=True),
        sa.Column("external_thread_id", sa.Text(), nullable=True),
        sa.Column("external_user_id", sa.Text(), nullable=True),
        sa.Column("external_message_id", sa.Text(), nullable=True),
        sa.Column("channel_name", sa.Text(), nullable=True),
        sa.Column("route_key", sa.Text(), nullable=True),
        sa.Column("routed_agent_id", sa.Uuid(), nullable=True),
        sa.Column("agent_display_name", sa.Text(), nullable=True),
        sa.Column("surface_event_metadata", jsonb, nullable=True),
        sa.Column("web_widget_id", sa.Uuid(), nullable=True),
        sa.Column("web_session_id", sa.Uuid(), nullable=True),
        sa.Column("held_replies", jsonb, nullable=True),
        sa.Column("free_text_answer_for", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
    )


def _backfill_surface_table(conn) -> None:
    _report(conn, "agent_conversations", _SURFACE_UNMAPPED)
    surfaces = conn.execute(sa.text(_SURFACE_BACKFILL)).rowcount
    _log().info("0066: backfilled %d surface_conversation_state rows.", surfaces)


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
        "0066: backfilled %d conversations, %d runs, %d messages.",
        conversations,
        runs,
        messages,
    )
    _log().info(
        "0066: %d later client_message_id copies dropped from the column; "
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

    _create_pauses()
    _backfill_pauses(conn)
    _create_agent_host_tables()
    _backfill_agent_host_tables(conn)
    _create_surface_table()
    _backfill_surface_table(conn)


def downgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(_RESTORE_WAITS))
    conn.execute(sa.text(_RESTORE_DECISIONS))
    conn.execute(sa.text(_RESTORE_SESSIONS))
    conn.execute(sa.text(_RESTORE_RUN_RECORDS))
    conn.execute(sa.text(_RESTORE_SURFACES))
    conn.execute(sa.text(_RESTORE_SCHEDULE_TYPES))
    op.drop_table("surface_conversation_state")
    op.drop_table("agent_host_run_records")
    op.drop_table("agent_host_sessions")
    op.drop_table("agent_pauses")
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
            ),
        ),
    ):
        for column in columns:
            op.drop_column(table, column)
