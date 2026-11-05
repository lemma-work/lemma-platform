"""Agents, approvals, waits, surfaces and pod bundles move onto the work runtime.

**The cutover replays nothing.** Deploy this release with the previous
release's workers stopped. What was durable state is armed; what was in flight
ends here, saying so where a person will look; nothing the old transport still
held -- its outbox rows, its streams' unread entries, its queued jobs -- is
converted or replayed, and ``0057`` drops it.

Armed (``cutover_statements``):

* an ACTIVE TIME or SUBAGENT agent wait -> ``agent.wait.fire`` at its
  ``scheduled_at``; an ACTIVE PROCESS wait -> ``agent.wait.poll``;
* an ACTIVE AGENT workflow wait -> ``workflow.wait_expire``, due now (it re-arms
  itself at the wait's real ceiling);
* an OPEN notification with an expiry -> ``notification.expire`` at it.

Ended:

* a RUNNING agent run -> FAILED ("Interrupted by the upgrade. Send a message to
  continue."), and its RUNNING conversation with it; a STOP_REQUESTED run and
  conversation -> STOPPED;
* a conversation WAITING on an approval that was decided but never executed
  -> FAILED: nothing would ever run the call now;
* an unfinished pod bundle job -> FAILED (``INTERRUPTED_BY_UPGRADE``), to be
  started again;
* a surface reply the old transport owed is forgotten.

From here a turn is started by the transaction that creates its run (an
``agent.turn`` item), and a sub-agent spawn is unique on the call that made it
(``agent_conversations.spawn_tool_call_id``, unique per parent), so a resumed
turn re-running an interrupted ``spawn_subagent`` gets the child it already has.

**Surfaces.** ``surface_inbound_messages`` (one row per inbound delivery,
unique on ``(receiver_scope, source_event_id)`` -- the dedup, for as long as
the row is kept: 7 days once handled, 30 once failed),
``surface_message_claims``, ``surface_deliveries`` (``PENDING -> SENDING ->
SENT | UNKNOWN | FAILED``) and ``surface_receiver_state`` replace the Redis
delivery claims, ``event_dedup`` keys, owed-reply metadata and receiver
cursors.

Every step is idempotent: the concurrent index builds commit on their own, so
a failure after them leaves the revision half applied, and a rerun must finish
it rather than trip over what is already there.

Revision ID: 0056_agent_surface_bundle_work
Revises: 0055_change_log_and_flows
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision = "0056_agent_surface_bundle_work"
down_revision = "0055_change_log_and_flows"
branch_labels = None
depends_on = None

#: The classes' per-pod caps; the runtime refreshes them from the limits port
#: within a minute, so these only have to be sane starting values.
_CAPS = {"events": 16, "automation": 32}

_ON_CONFLICT = (
    "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
    "DO NOTHING"
)

#: What a person sees on a run the upgrade interrupted.
_INTERRUPTED = "Interrupted by the upgrade. Send a message to continue."


# -- agent turns ---------------------------------------------------------------

_WAIT_NS = "'pod:' || w.pod_id::text"
_AGENT_WAITS = (
    "workflow_run_waits w WHERE w.status = 'ACTIVE' AND w.wait_type = 'AGENT'"
)


def _repair_invalid(name: str) -> None:
    """Drop a half-built index so `IF NOT EXISTS` stops mistaking it for done.

    An interrupted `CREATE INDEX CONCURRENTLY` leaves the index behind with
    `indisvalid = false`. The name exists, so on the rerun `IF NOT EXISTS`
    skips the build, the revision is stamped, and the planner never uses the
    index -- or, for a unique one, never enforces it. Checking first is what
    makes a rerun recover (the same guard as 0038 and 0053).
    """
    half_built = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": name},
        )
        .scalar()
    )
    if half_built:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def _queues(cls: str, ns: str, source: str) -> str:
    return (
        "INSERT INTO work_queues (class, ns, max_running) "
        f"SELECT DISTINCT '{cls}', {ns}, {_CAPS[cls]} FROM {source} "
        "ON CONFLICT (class, ns) DO NOTHING"
    )


_AGENT_TURN_KINDS = (
    "'agent.turn', 'agent.turn.automation', 'agent.title', 'agent.followup', "
    "'agent.subagent_result', 'agent.replies_arrived', 'pod_ask.deliver', "
    "'workflow.resume_for_agent', 'schedule.agent_finished'"
)


# -- approvals and waits -------------------------------------------------------

_TIMERS = (
    "agent_conversation_waits w WHERE w.status = 'ACTIVE' "
    "AND w.wait_type IN ('TIME', 'SUBAGENT')"
)
_POLLS = (
    "agent_conversation_waits w WHERE w.status = 'ACTIVE' AND w.wait_type = 'PROCESS'"
)

#: Decisions whose paused call exists and has no return yet.
_OPEN_DECISIONS = (
    "agent_approval_decisions d "
    "JOIN agent_conversations c ON c.id = d.conversation_id "
    "WHERE EXISTS (SELECT 1 FROM agent_messages m "
    " WHERE m.conversation_id = d.conversation_id "
    " AND m.tool_call_id = d.approval_id AND m.kind = 'TOOL_CALL') "
    "AND NOT EXISTS (SELECT 1 FROM agent_messages m "
    " WHERE m.conversation_id = d.conversation_id "
    " AND m.tool_call_id = d.approval_id AND m.kind = 'TOOL_RETURN')"
)
_NOTICES = "notifications n WHERE n.status = 'OPEN' AND n.expires_at IS NOT NULL"


def cutover_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them.

    Live state is armed: an agent's waits, a workflow's AGENT waits and an open
    notification's expiry each get the item that ends them. Work in flight is
    not resumed: a running turn, a decided-but-unexecuted approval, a bundle
    job and an owed reply end here, saying so where a person will look.
    """
    wait_payload = "jsonb_build_object('wait_id', w.id::text)"
    wait_dedup = "'wait:' || w.id::text"
    wait_items = (
        "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
        "SELECT gen_random_uuid(), {ns}, '{cls}', '{kind}', {payload}, {dedup}, "
        "{run_at} FROM {source} {conflict}"
    )
    ended_runs = (
        "WITH ended AS ("
        "UPDATE agent_runs r SET status = CASE WHEN r.status = 'STOP_REQUESTED' "
        "THEN 'STOPPED' ELSE 'FAILED' END, finished_at = now(), "
        "error = CASE WHEN r.status = 'STOP_REQUESTED' THEN r.error ELSE "
        f"'{_INTERRUPTED}' END "
        "WHERE r.status IN ('RUNNING', 'STOP_REQUESTED') "
        "RETURNING r.conversation_id, r.status) "
        "UPDATE agent_conversations c SET status = ended.status FROM ended "
        "WHERE c.id = ended.conversation_id "
        "AND c.status IN ('RUNNING', 'STOP_REQUESTED')"
    )
    # A decided call with no item to run it: its conversation would wait on
    # it forever.
    stranded_approvals = (
        "UPDATE agent_conversations c SET status = 'FAILED' "
        "WHERE c.status = 'WAITING' AND c.id IN ("
        f"SELECT d.conversation_id FROM {_OPEN_DECISIONS})"
    )
    ended_bundles = (
        "UPDATE pod_bundle_jobs j SET status = 'FAILED', completed_at = now(), "
        "error_code = 'INTERRUPTED_BY_UPGRADE', "
        "error = 'Interrupted by the upgrade. Start it again to retry.' "
        "WHERE j.status IN ('QUEUED', 'EXPORTING', 'APPLYING', 'FETCHING', "
        "'PLANNING', 'PUBLISHING')"
    )
    owed_replies = (
        "UPDATE agent_conversations SET conversation_metadata = "
        "conversation_metadata - 'surface_owed_reply' "
        "WHERE conversation_metadata ? 'surface_owed_reply'"
    )
    notice_expiry = (
        "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
        "SELECT gen_random_uuid(), 'pod:' || n.pod_id::text, 'events', "
        "'notification.expire', jsonb_build_object('notification_id', n.id::text), "
        "'notification-expire:' || n.id::text, n.expires_at "
        f"FROM {_NOTICES} {_ON_CONFLICT}"
    )
    return [
        # -- in flight: ended ----------------------------------------------
        ended_runs,
        stranded_approvals,
        ended_bundles,
        owed_replies,
        # -- live state: armed ---------------------------------------------
        _queues("events", _WAIT_NS, _TIMERS),
        _queues("automation", _WAIT_NS, _POLLS),
        _queues("events", _WAIT_NS, _AGENT_WAITS),
        wait_items.format(
            ns=_WAIT_NS,
            cls="events",
            kind="agent.wait.fire",
            payload=wait_payload,
            dedup=wait_dedup,
            run_at="COALESCE(w.scheduled_at, now())",
            source=_TIMERS,
            conflict=_ON_CONFLICT,
        ),
        wait_items.format(
            ns=_WAIT_NS,
            cls="automation",
            kind="agent.wait.poll",
            payload=wait_payload,
            dedup=wait_dedup,
            run_at="COALESCE(w.scheduled_at, now())",
            source=_POLLS,
            conflict=_ON_CONFLICT,
        ),
        wait_items.format(
            ns=_WAIT_NS,
            cls="events",
            kind="workflow.wait_expire",
            payload=wait_payload,
            dedup="'workflow-expire:' || w.id::text",
            run_at="now()",
            source=_AGENT_WAITS,
            conflict=_ON_CONFLICT,
        ),
        _queues("events", "'pod:' || n.pod_id::text", _NOTICES),
        notice_expiry,
    ]


_AGENT_WAIT_KINDS = (
    "'agent.wait.fire', 'agent.wait.poll', 'agent.approval.execute', "
    "'notification.expire'"
)


# -- surfaces -------------------------------------------------------------------


def _now(name: str, *, nullable: bool = False) -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=nullable,
        server_default=sa.text("now()"),
    )


def _create_surface_tables() -> None:
    op.create_table(
        "surface_inbound_messages",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("receiver_scope", sa.Text(), nullable=False),
        sa.Column("source_event_id", sa.Text(), nullable=False),
        sa.Column("platform", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("surface_id", UUID(as_uuid=True), nullable=True),
        sa.Column("pod_id", UUID(as_uuid=True), nullable=True),
        sa.Column("payload", JSONB(), nullable=False),
        sa.Column(
            "headers", JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        sa.Column("receiver_surface_ids", JSONB(), nullable=True),
        sa.Column("thread_key", sa.Text(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'RECEIVED'")
        ),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", UUID(as_uuid=True), nullable=True),
        _now("received_at"),
        sa.Column("routed_at", sa.DateTime(timezone=True), nullable=True),
        _now("updated_at"),
        sa.CheckConstraint(
            "status IN ('RECEIVED', 'INGESTING', 'ROUTED', 'DROPPED', 'FAILED')",
            name="ck_surface_inbound_messages_status",
        ),
        sa.UniqueConstraint(
            "receiver_scope",
            "source_event_id",
            name="uq_surface_inbound_messages_receiver_event",
        ),
        if_not_exists=True,
    )
    # Retention reads finished rows by age.
    op.create_index(
        "ix_surface_inbound_messages_status_received",
        "surface_inbound_messages",
        ["status", "received_at"],
        if_not_exists=True,
    )

    op.create_table(
        "surface_message_claims",
        sa.Column("claim_key", sa.Text(), primary_key=True),
        sa.Column("owner", sa.Text(), nullable=True),
        _now("created_at"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_surface_message_claims_created_at",
        "surface_message_claims",
        ["created_at"],
        if_not_exists=True,
    )

    op.create_table(
        "surface_deliveries",
        sa.Column("id", UUID(as_uuid=True), primary_key=True),
        sa.Column("idempotency_key", sa.Text(), nullable=False),
        sa.Column("pod_id", UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", UUID(as_uuid=True), nullable=True),
        sa.Column("agent_run_id", UUID(as_uuid=True), nullable=True),
        sa.Column("notification_id", UUID(as_uuid=True), nullable=True),
        sa.Column("inbound_message_id", UUID(as_uuid=True), nullable=True),
        sa.Column("surface_id", UUID(as_uuid=True), nullable=True),
        sa.Column("destination_key", sa.Text(), nullable=False),
        sa.Column("target", JSONB(), nullable=False),
        sa.Column("content", JSONB(), nullable=False),
        sa.Column(
            "status", sa.Text(), nullable=False, server_default=sa.text("'PENDING'")
        ),
        sa.Column(
            "attempts", sa.Integer(), nullable=False, server_default=sa.text("0")
        ),
        _now("not_before"),
        sa.Column("sending_until", sa.DateTime(timezone=True), nullable=True),
        sa.Column("provider_message_id", sa.Text(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        _now("created_at"),
        _now("updated_at"),
        sa.CheckConstraint(
            "status IN ('PENDING', 'SENDING', 'SENT', 'UNKNOWN', 'FAILED')",
            name="ck_surface_deliveries_status",
        ),
        sa.UniqueConstraint(
            "idempotency_key", name="uq_surface_deliveries_idempotency_key"
        ),
        if_not_exists=True,
    )
    # The destination pump: the oldest unfinished delivery for one destination.
    op.create_index(
        "ix_surface_deliveries_destination_open",
        "surface_deliveries",
        ["destination_key", "created_at"],
        postgresql_where=sa.text("status IN ('PENDING', 'SENDING')"),
        if_not_exists=True,
    )
    op.create_index(
        "ix_surface_deliveries_status_created",
        "surface_deliveries",
        ["status", "created_at"],
        if_not_exists=True,
    )

    op.create_table(
        "surface_receiver_state",
        sa.Column("receiver_key", sa.Text(), primary_key=True),
        sa.Column("cursor", sa.Text(), nullable=True),
        _now("updated_at"),
        if_not_exists=True,
    )


def _drop_surface_tables() -> None:
    op.drop_table("surface_receiver_state")
    op.drop_index(
        "ix_surface_deliveries_status_created", table_name="surface_deliveries"
    )
    op.drop_index(
        "ix_surface_deliveries_destination_open", table_name="surface_deliveries"
    )
    op.drop_table("surface_deliveries")
    op.drop_index(
        "ix_surface_message_claims_created_at", table_name="surface_message_claims"
    )
    op.drop_table("surface_message_claims")
    op.drop_index(
        "ix_surface_inbound_messages_status_received",
        table_name="surface_inbound_messages",
    )
    op.drop_table("surface_inbound_messages")


# -- pod bundles ----------------------------------------------------------------

_BUNDLE_KINDS = (
    "pod_bundle.export",
    "pod_bundle.apply",
    "pod_bundle.plan",
    "pod_bundle.import_github",
    "pod_bundle.publish",
)


def upgrade() -> None:
    op.add_column(
        "agent_conversations",
        sa.Column("spawn_tool_call_id", sa.String(300), nullable=True),
        if_not_exists=True,
    )
    _create_surface_tables()
    for statement in cutover_statements():
        op.execute(sa.text(statement))
    # Concurrently, outside the transaction: conversations are written on
    # every turn, and a plain build would block those writers.
    with op.get_context().autocommit_block():
        _repair_invalid("uq_agent_conv_spawn_call")
        op.create_index(
            "uq_agent_conv_spawn_call",
            "agent_conversations",
            ["parent_id", "spawn_tool_call_id"],
            unique=True,
            postgresql_where=sa.text("spawn_tool_call_id IS NOT NULL"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "uq_agent_conv_spawn_call",
            table_name="agent_conversations",
            postgresql_concurrently=True,
            if_exists=True,
        )
    bind = op.get_bind()
    # Items only, for bundles: the jobs' rows are the old transport's state too.
    bind.execute(
        sa.text("DELETE FROM work_items WHERE kind = ANY(:kinds) AND state = 'queued'"),
        {"kinds": list(_BUNDLE_KINDS)},
    )
    op.execute(sa.text(f"DELETE FROM work_items WHERE kind IN ({_AGENT_WAIT_KINDS})"))
    op.execute(sa.text(f"DELETE FROM work_items WHERE kind IN ({_AGENT_TURN_KINDS})"))
    _drop_surface_tables()
    op.drop_column("agent_conversations", "spawn_tool_call_id")
