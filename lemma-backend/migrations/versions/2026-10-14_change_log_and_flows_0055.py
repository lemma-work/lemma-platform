"""Record changes, triggers, workflows and functions move onto the work runtime.

**Record change log.** DATASTORE triggers and MCP event subscriptions used to
read ``datastore.events`` through two consumer groups; now each is a durable
subscriber of its pod's change log (created per pod by the pod-schema DDL
runner, not here). One table here:

* ``mcp_event_deliveries`` -- one row per record change owed to one MCP event
  subscription, unique on ``(subscription, change)``, written by the change
  log's delivery in the transaction that decides the change matches.

Every active DATASTORE schedule and every live MCP subscription gets a
``changes.sync_subscribers`` item, which creates its subscriber row at the
log's current horizon: it is owed what is written from then on. What the old
path still held -- datastore outbox rows, the two groups' unread entries -- is
not converted; the release is deployed with the previous one's workers
stopped, and those record changes fire nothing.

**Triggers.** Two tables and one column:

* ``trigger_skips`` -- what a trigger let pass without firing (a bulk write, an
  import, its daily ceiling), one row per page and reason rather than per
  change;
* ``trigger_batches`` -- the window a ``batch`` trigger planned before firing
  it, unique on its first change, so a replay fires the same window;
* ``schedules.paused_reason`` -- why the platform paused a schedule.

TIME schedules were fired by a poller reading ``next_fire_at``; now each owns a
``schedule.fire`` item. Every active TIME schedule gets a ``schedule.arm`` item,
which writes that fire item from the row's cursor (computing one, or retiring
the row, where it has none) -- the cron arithmetic lives in the application,
not in SQL. A fire already due when the release goes out is skipped, not fired
late. A schedule run the old path never saw to its target ends as
``TARGET_FAILED`` (``INTERRUPTED_BY_UPGRADE``), which the failure breaker passes
over and a person may redrive.

**Functions and workflows.** Function runs were started by a post-commit
streaq enqueue repaired by a once-a-minute sweep, held a pod slot in a Redis
sorted set, and were failed at their deadline by the same sweep. Workflow
waits were woken through a consumer group, the schedule poller and a
five-minute reconciliation. Each of those is now a work item written with the
state that needs it. At the cutover an unfinished function run is failed
("Interrupted by the upgrade"), and the workflow waits are armed:

* an ACTIVE TIME wait -> ``workflow.resume`` at its ``scheduled_at``;
* an ACTIVE FUNCTION wait -> ``workflow.resume`` now: its run has just ended;
* an ACTIVE DECISION wait -> ``workflow.decide``;
* every ACTIVE FUNCTION, DECISION and HUMAN wait -> ``workflow.wait_expire``,
  due now, which re-arms itself at the wait's real ceiling;
* a workflow with parked (QUEUED) runs -> ``workflow.admit``.

AGENT waits are armed by ``0056``. Two indexes served the deleted sweeps; one
is added for the per-pod gate, which counts a pod's RUNNING runs at every
start.

**Existing triggers keep firing on bulk and import writes.** Before the change
log every record write -- bulk and import included -- fired every matching
trigger. A trigger now says what a bulk or import write does to it
(``bulk_writes`` / ``import_writes`` in its config), and leaving both unset
means ``skip``. A trigger that predates the setting was written expecting the
old behaviour, so each one that sets neither is given ``each``. New triggers
keep the ``skip`` default. The downgrade leaves the configs as they are:
``each`` is what these triggers did before the setting existed.

Every step is idempotent: the concurrent index builds commit on their own, so
a failure after them leaves the revision half applied, and a rerun must finish
it rather than trip over what is already there.

Revision ID: 0055_change_log_and_flows
Revises: 0054_work_runtime
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.engine import Connection

revision = "0055_change_log_and_flows"
down_revision = "0054_work_runtime"
branch_labels = None
depends_on = None

#: The classes' per-pod caps; the runtime refreshes them from the limits port
#: within a minute, so these only have to be sane starting values.
_CAPS = {"events": 16, "automation": 32}

_NS = "CASE WHEN s.pod_id IS NULL THEN 'system' ELSE 'pod:' || s.pod_id::text END"

_ACTIVE_TIME = "s.schedule_type = 'TIME' AND s.is_active"

#: A schedule run the old path received or started and never saw to its
#: target: it ends here instead of being started again.
_UNFINISHED_RUN = (
    "r.target_outcome IS NULL AND r.status IN ('RECEIVED', 'FILTERING', 'PROCESSING')"
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


def trigger_seed_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them."""
    queues = (
        "INSERT INTO work_queues (class, ns, max_running) "
        f"SELECT DISTINCT 'events', {_NS}, {_CAPS['events']} "
        f"FROM schedules s WHERE {_ACTIVE_TIME} "
        "ON CONFLICT (class, ns) DO NOTHING"
    )
    # A fire already due is skipped, not fired late: arm computes the next one
    # from now.
    missed = (
        "UPDATE schedules s SET next_fire_at = NULL "
        f"WHERE {_ACTIVE_TIME} AND s.next_fire_at <= now()"
    )
    arm = (
        "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
        f"SELECT gen_random_uuid(), {_NS}, 'events', 'schedule.arm', "
        "jsonb_build_object('schedule_id', s.id::text), "
        "'schedule-arm:' || s.id::text, now() "
        f"FROM schedules s WHERE {_ACTIVE_TIME} "
        "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
        "DO NOTHING"
    )
    unfinished = (
        "UPDATE schedule_runs r SET status = 'TARGET_FAILED', "
        "error_code = 'INTERRUPTED_BY_UPGRADE', "
        # The breaker passes over this error type (DEPLOYMENT_FAILURES): the
        # upgrade ended these, no target of the schedule failed.
        "error_type = 'ScheduleNotStartedBeforeUpgrade', "
        f"completed_at = now() WHERE {_UNFINISHED_RUN}"
    )
    return [queues, missed, arm, unfinished]


_ON_CONFLICT = (
    "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
    "DO NOTHING"
)

_WAIT_NS = "'pod:' || w.pod_id::text"
_ACTIVE_WAIT = "w.status = 'ACTIVE'"


def _item(
    *, cls: str, kind: str, ns: str, payload: str, dedup: str, run_at: str, source: str
) -> str:
    return (
        "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, run_at) "
        f"SELECT gen_random_uuid(), {ns}, '{cls}', '{kind}', {payload}, {dedup}, "
        f"{run_at} FROM {source} {_ON_CONFLICT}"
    )


def _queues(cls: str, ns: str, source: str) -> str:
    return (
        "INSERT INTO work_queues (class, ns, max_running) "
        f"SELECT DISTINCT '{cls}', {ns}, {_CAPS[cls]} FROM {source} "
        "ON CONFLICT (class, ns) DO NOTHING"
    )


def flow_seed_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them."""
    # Ended first, so the FUNCTION waits below find every run they wait on
    # finished and resume their workflows with the failure.
    ended_runs = (
        "UPDATE function_runs r SET status = 'FAILED', completed_at = now(), "
        "error = 'Interrupted by the upgrade. Run it again to retry.' "
        "WHERE r.status IN ('PENDING', 'RUNNING')"
    )
    timers = (
        f"workflow_run_waits w WHERE {_ACTIVE_WAIT} AND w.wait_type = 'TIME' "
        "AND w.external_ref IS NOT NULL"
    )
    finished_functions = (
        f"workflow_run_waits w WHERE {_ACTIVE_WAIT} AND w.wait_type = 'FUNCTION' "
        "AND w.external_ref IS NOT NULL AND NOT EXISTS ("
        " SELECT 1 FROM function_runs r WHERE r.id::text = w.external_ref"
        " AND r.status IN ('PENDING', 'RUNNING'))"
    )
    decisions = (
        f"workflow_run_waits w WHERE {_ACTIVE_WAIT} AND w.wait_type = 'DECISION' "
        "AND w.external_ref IS NOT NULL"
    )
    expiring = (
        f"workflow_run_waits w WHERE {_ACTIVE_WAIT} "
        "AND w.wait_type IN ('FUNCTION', 'DECISION', 'HUMAN')"
    )
    parked = (
        f"(SELECT DISTINCT w.flow_id, w.pod_id FROM workflow_run_waits w "
        f"WHERE {_ACTIVE_WAIT} AND w.wait_type = 'QUEUED') w"
    )
    wait_sources = (timers, finished_functions, decisions, expiring, parked)
    return [
        ended_runs,
        *(_queues("events", _WAIT_NS, source) for source in wait_sources),
        _queues("automation", _WAIT_NS, decisions),
        _item(
            cls="events",
            kind="workflow.resume",
            ns=_WAIT_NS,
            payload=(
                "jsonb_build_object('wait_type', 'TIME', 'external_ref', "
                "w.external_ref)"
            ),
            dedup="'workflow-resume:' || w.external_ref",
            run_at="COALESCE(w.scheduled_at, now())",
            source=timers,
        ),
        _item(
            cls="events",
            kind="workflow.resume",
            ns=_WAIT_NS,
            payload="jsonb_build_object('function_run_id', w.external_ref)",
            dedup="'workflow-resume:fn:' || w.external_ref",
            run_at="now()",
            source=finished_functions,
        ),
        _item(
            cls="automation",
            kind="workflow.decide",
            ns=_WAIT_NS,
            payload="jsonb_build_object('external_ref', w.external_ref)",
            dedup="'workflow-decide:' || w.external_ref",
            run_at="now()",
            source=decisions,
        ),
        _item(
            cls="events",
            kind="workflow.wait_expire",
            ns=_WAIT_NS,
            payload="jsonb_build_object('wait_id', w.id::text)",
            dedup="'workflow-expire:' || w.id::text",
            run_at="now()",
            source=expiring,
        ),
        _item(
            cls="events",
            kind="workflow.admit",
            ns=_WAIT_NS,
            payload="jsonb_build_object('flow_id', w.flow_id::text)",
            dedup="'workflow-admit:' || w.flow_id::text",
            run_at="now()",
            source=parked,
        ),
    ]


_SEEDED_KINDS = (
    "'function.run', 'function.cancel', 'function.deadline', 'workflow.resume', "
    "'workflow.decide', 'workflow.wait_expire', 'workflow.admit', "
    "'workflow.notify_form', 'workflow.start'"
)


_DATASTORE_TRIGGERS = (
    "SELECT s.pod_id, 'schedule:' || s.id::text AS subscriber FROM schedules s "
    "WHERE s.schedule_type = 'DATASTORE' AND s.is_active AND s.pod_id IS NOT NULL"
)
_MCP_SUBSCRIPTIONS = (
    "SELECT m.pod_id, 'mcp:' || m.public_id AS subscriber "
    "FROM mcp_event_subscriptions m "
    "WHERE m.stopped_at IS NULL AND m.refresh_before > now()"
)


def change_log_seed_statements() -> list[str]:
    """The cutover, as statements -- importable so a test can run them."""
    subscribers = f"({_DATASTORE_TRIGGERS} UNION ALL {_MCP_SUBSCRIPTIONS}) AS w"
    return [
        (
            "INSERT INTO work_queues (class, ns, max_running) "
            f"SELECT DISTINCT 'events', 'pod:' || w.pod_id::text, {_CAPS['events']} "
            f"FROM {subscribers} ON CONFLICT (class, ns) DO NOTHING"
        ),
        (
            "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, "
            "concurrency_key, concurrency_limit, run_at) "
            "SELECT gen_random_uuid(), 'pod:' || w.pod_id::text, 'events', "
            "'changes.sync_subscribers', "
            "jsonb_build_object('pod_id', w.pod_id::text, 'subscriber', w.subscriber), "
            "'changes.sync:' || w.pod_id::text || ':' || w.subscriber, "
            "'changes:' || w.pod_id::text || ':' || w.subscriber, 1, now() "
            f"FROM {subscribers} "
            "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL AND state = 'queued' "
            "DO NOTHING"
        ),
    ]


def _backfill_trigger_write_modes(conn: Connection) -> None:
    """Every DATASTORE trigger setting neither write mode fires on each row.

    Kept apart from the rest of the migration so it can be folded into another
    revision unchanged. The schedules it touches are the ones it re-syncs: the
    update runs first, in a CTE, and its ``RETURNING`` is the sync's input.
    """
    conn.execute(
        sa.text(
            "WITH changed AS ("
            "UPDATE schedules SET config = COALESCE(config, '{}'::jsonb) "
            "|| jsonb_build_object('bulk_writes', 'each', 'import_writes', 'each') "
            "WHERE schedule_type = 'DATASTORE' "
            "AND NOT (COALESCE(config, '{}'::jsonb) "
            "?| ARRAY['bulk_writes', 'import_writes']) "
            "RETURNING id, pod_id, is_active), "
            "watching AS ("
            "SELECT pod_id, 'schedule:' || id::text AS subscriber FROM changed "
            "WHERE is_active AND pod_id IS NOT NULL), "
            "queues AS ("
            "INSERT INTO work_queues (class, ns, max_running) "
            "SELECT DISTINCT 'events', 'pod:' || pod_id::text, 16 FROM watching "
            "ON CONFLICT (class, ns) DO NOTHING) "
            "INSERT INTO work_items (id, ns, class, kind, payload, dedup_key, "
            "concurrency_key, concurrency_limit, run_at) "
            "SELECT gen_random_uuid(), 'pod:' || pod_id::text, 'events', "
            "'changes.sync_subscribers', "
            "jsonb_build_object('pod_id', pod_id::text, 'subscriber', subscriber), "
            "'changes.sync:' || pod_id::text || ':' || subscriber, "
            "'changes:' || pod_id::text || ':' || subscriber, 1, now() "
            "FROM watching "
            "ON CONFLICT (dedup_key) WHERE dedup_key IS NOT NULL "
            "AND state = 'queued' DO NOTHING"
        )
    )


def _create_trigger_tables() -> None:
    op.add_column(
        "schedules",
        sa.Column("paused_reason", sa.String(32), nullable=True),
        if_not_exists=True,
    )
    op.create_table(
        "trigger_skips",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Uuid(),
            sa.ForeignKey("schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("reason", sa.String(32), nullable=False),
        sa.Column("first", sa.String(255), nullable=False),
        sa.Column("last", sa.String(255), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column(
            "at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "schedule_id", "reason", "first", name="uq_trigger_skips_page"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_trigger_skips_schedule_at",
        "trigger_skips",
        ["schedule_id", "at"],
        if_not_exists=True,
    )
    op.create_table(
        "trigger_batches",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "schedule_id",
            sa.Uuid(),
            sa.ForeignKey("schedules.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("first", sa.String(255), nullable=False),
        sa.Column("last", sa.String(255), nullable=False),
        sa.Column("count", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint("schedule_id", "first", name="uq_trigger_batches_first"),
        if_not_exists=True,
    )


def _create_mcp_deliveries() -> None:
    op.create_table(
        "mcp_event_deliveries",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "subscription_id",
            sa.Uuid(),
            sa.ForeignKey("mcp_event_subscriptions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("change_key", sa.String(80), nullable=False),
        sa.Column("pod_id", sa.Uuid(), nullable=False),
        sa.Column("table_name", sa.String(255), nullable=False),
        sa.Column("record_id", sa.Text(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("outcome", sa.String(16), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "subscription_id", "change_key", name="uq_mcp_event_deliveries_change"
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_mcp_event_deliveries_created",
        "mcp_event_deliveries",
        ["created_at"],
        if_not_exists=True,
    )


def upgrade() -> None:
    _create_trigger_tables()
    _create_mcp_deliveries()
    _backfill_trigger_write_modes(op.get_bind())
    for statement in (
        *trigger_seed_statements(),
        *flow_seed_statements(),
        *change_log_seed_statements(),
    ):
        op.execute(sa.text(statement))
    # Concurrently, outside the transaction: function_runs is written on
    # every run, and a plain build blocks those writers for the scan.
    with op.get_context().autocommit_block():
        _repair_invalid("ix_function_runs_running")
        op.create_index(
            "ix_function_runs_running",
            "function_runs",
            ["function_id"],
            postgresql_where=sa.text("status = 'RUNNING'"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        for name in ("ix_function_runs_expiring", "ix_function_runs_pending_async"):
            op.drop_index(
                name,
                table_name="function_runs",
                postgresql_concurrently=True,
                if_exists=True,
            )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        _repair_invalid("ix_function_runs_expiring")
        op.create_index(
            "ix_function_runs_expiring",
            "function_runs",
            ["deadline_at", "id"],
            postgresql_where=sa.text(
                "deadline_at IS NOT NULL AND status IN ('PENDING', 'RUNNING')"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        _repair_invalid("ix_function_runs_pending_async")
        op.create_index(
            "ix_function_runs_pending_async",
            "function_runs",
            ["created_at", "id"],
            postgresql_where=sa.text(
                "status = 'PENDING' AND job_id IS NOT NULL AND deadline_at IS NOT NULL"
            ),
            postgresql_concurrently=True,
            if_not_exists=True,
        )
        op.drop_index(
            "ix_function_runs_running",
            table_name="function_runs",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.execute(sa.text(f"DELETE FROM work_items WHERE kind IN ({_SEEDED_KINDS})"))
    op.execute(
        "DELETE FROM work_items WHERE kind IN ('changes.sync_subscribers', "
        "'changes.deliver', 'changes.retention', 'mcp.deliver')"
    )
    op.drop_index("ix_mcp_event_deliveries_created", table_name="mcp_event_deliveries")
    op.drop_table("mcp_event_deliveries")
    op.execute(
        sa.text(
            "DELETE FROM work_items WHERE kind IN ('schedule.arm', 'schedule.fire', "
            "'schedule.start_target', 'schedule.filter', 'schedule.outcome', "
            "'schedule.paused_notice')"
        )
    )
    op.drop_table("trigger_batches")
    op.drop_index("ix_trigger_skips_schedule_at", table_name="trigger_skips")
    op.drop_table("trigger_skips")
    op.drop_column("schedules", "paused_reason")
