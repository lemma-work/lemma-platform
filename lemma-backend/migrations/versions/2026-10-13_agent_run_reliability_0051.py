"""Agent run reliability: resumable Agent Host runs, a Telegram identity that
must be proven, workflow concurrency caps, sound runtime profiles, and one
spelling for each stored status.

**Agent Host run resume.** A worker stopped mid-run -- a deploy, a SIGTERM --
leaves the run RUNNING for the next worker to reclaim. Two nullable columns on
the run's lease let that worker carry on instead of replaying the run:

* ``consumed_stream_id`` -- the last stream entry the previous worker fully
  persisted. The next worker resumes reading after it, and the stream is only
  trimmed below it, so an entry no worker has persisted is never dropped.
* ``credential_expires_at`` -- when the Lemma credential the host holds for the
  run expires, kept current as refreshes land, so a resumed worker keeps
  renewing it rather than minting a replacement.

Null on every existing lease, which reads as "from the start" and "unknown".

**Telegram identity is proven, never claimed.** A profile's free-text Telegram
handle is no longer read: anybody can type a handle onto their own profile, and
a handle given up on Telegram is taken by a stranger. A managed bot's setup
link no longer binds its opener either: a link can leak. A Telegram account is
bound to a person only by sharing its contact against a verified number. So:

* a cached Telegram sender with a person on it and no number on the row can
  only have been bound by a handle or a setup link; it is unbound, and the
  sender is asked to share their contact once on their next message;
* every stored handle is cleared, and its index dropped. The column itself
  stays, unused: a pod still running the previous release selects it until the
  rollout finishes, and finds nothing in it.

**Workflow concurrency.** ``workflow_flows.max_concurrent_runs`` caps how many
of a workflow's runs are in progress at once. Nullable with no default, so every
existing workflow keeps running unlimited; the check matches the API's bound, so
a value written behind its back cannot become a cap of zero.

**Runtime profiles.**

* ``version``, a counter for optimistic locking of profile writes, from 0.
* At most one profile carries the organization's default mark (a key in
  ``profile_metadata``). Organizations with more than one keep the most
  recently updated mark; the others are cleared. A partial unique index then
  holds it.
* A personal profile is its owner's credential, and now goes with them: the
  foreign key to ``users`` cascades instead of setting NULL. Personal profiles
  already orphaned that way -- readable by nobody -- are deleted first. A lease
  that names one keeps its row and loses the reference, as it already would.
* A coding-agent profile stored with ``host_wait_timeout_seconds: null`` has the
  key removed, so the default applies rather than a null being read as one.

**One spelling per status.** Run and conversation statuses, message kinds and
approval decisions are written in capitals, and some rows from before that are
lowercase; reads have accepted both. Those rows are rewritten in capitals. Only
rows that differ are touched. Message roles are lowercase by design and are not
among them.

Locks and rollout: every change is to a small table except the status rewrite,
which scans ``agent_runs``, ``agent_conversations``, ``agent_messages`` and
``agent_approval_decisions`` under ordinary row locks, blocking nothing but a
writer of the same row. The foreign key change validates against ``users``
under a SHARE ROW EXCLUSIVE lock for the length of a scan of the (small)
profiles table. The previous release reads everything this leaves behind.

Revision ID: 0051_agent_run_reliability
Revises: 0050_pod_grantee_index
"""

import sqlalchemy as sa
from alembic import op

revision = "0051_agent_run_reliability"
down_revision = "0050_pod_grantee_index"
branch_labels = None
depends_on = None

_PROFILE_USER_FK = "agent_runtime_profiles_user_id_fkey"
_ORG_DEFAULT_INDEX = "uq_agent_runtime_profile_org_default"
_ORG_DEFAULT_MARK = "organization_default"
_FLOW_CAP_CHECK = "ck_workflow_flows_max_concurrent_runs_positive"

#: (table, column) pairs read case-insensitively before this.
_UPPERCASE_STATUSES = (
    ("agent_runs", "status"),
    ("agent_conversations", "status"),
    ("agent_messages", "kind"),
    ("agent_approval_decisions", "decision"),
)


def upgrade() -> None:
    _run_resume_columns()
    _telegram_identity()
    _workflow_concurrency()
    _runtime_profiles()
    _uppercase_statuses()


def _run_resume_columns() -> None:
    op.add_column(
        "agent_host_run_leases",
        sa.Column("consumed_stream_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "agent_host_run_leases",
        sa.Column("credential_expires_at", sa.DateTime(timezone=True), nullable=True),
    )


def _telegram_identity() -> None:
    op.execute(
        """
        UPDATE agent_surface_external_users
        SET resolved_user_id = NULL
        WHERE platform = 'TELEGRAM'
          AND resolved_user_id IS NOT NULL
          AND phone IS NULL
        """
    )
    op.execute(
        "UPDATE users SET telegram_username = NULL WHERE telegram_username IS NOT NULL"
    )
    op.execute(sa.text("DROP INDEX IF EXISTS ix_users_telegram_username"))


def _workflow_concurrency() -> None:
    op.add_column(
        "workflow_flows",
        sa.Column("max_concurrent_runs", sa.Integer(), nullable=True),
    )
    op.create_check_constraint(
        _FLOW_CAP_CHECK,
        "workflow_flows",
        "max_concurrent_runs IS NULL OR max_concurrent_runs >= 1",
    )


def _runtime_profiles() -> None:
    op.add_column(
        "agent_runtime_profiles",
        sa.Column("version", sa.Integer(), nullable=False, server_default=sa.text("0")),
    )

    # Keep each organization's most recently updated mark; clear the rest.
    op.execute(
        f"""
        UPDATE agent_runtime_profiles AS profile
        SET profile_metadata = profile.profile_metadata - '{_ORG_DEFAULT_MARK}'
        FROM (
            SELECT id, row_number() OVER (
                PARTITION BY organization_id
                ORDER BY updated_at DESC, id DESC
            ) AS rank
            FROM agent_runtime_profiles
            WHERE profile_metadata ? '{_ORG_DEFAULT_MARK}'
        ) AS marked
        WHERE profile.id = marked.id AND marked.rank > 1
        """
    )
    op.create_index(
        _ORG_DEFAULT_INDEX,
        "agent_runtime_profiles",
        ["organization_id"],
        unique=True,
        postgresql_where=sa.text(f"profile_metadata ? '{_ORG_DEFAULT_MARK}'"),
    )

    op.execute(
        "DELETE FROM agent_runtime_profiles "
        "WHERE scope = 'PERSONAL' AND user_id IS NULL"
    )
    _replace_profile_user_fk(ondelete="CASCADE")

    op.execute(
        """
        UPDATE agent_runtime_profiles
        SET config = config - 'host_wait_timeout_seconds'
        WHERE kind = 'HARNESS'
          AND jsonb_typeof(config -> 'host_wait_timeout_seconds') = 'null'
        """
    )


def _replace_profile_user_fk(*, ondelete: str) -> None:
    """Swap the profile's ``user_id`` foreign key for one with ``ondelete``.

    The baseline created it unnamed, so its name is the server's choice; it is
    looked up rather than assumed, and the replacement is named explicitly.
    """
    op.execute(
        """
        DO $$
        DECLARE existing text;
        BEGIN
            SELECT con.conname INTO existing
            FROM pg_constraint AS con
            JOIN pg_attribute AS att
              ON att.attrelid = con.conrelid AND att.attnum = ANY (con.conkey)
            WHERE con.conrelid = 'agent_runtime_profiles'::regclass
              AND con.contype = 'f'
              AND att.attname = 'user_id';
            IF existing IS NOT NULL THEN
                EXECUTE format(
                    'ALTER TABLE agent_runtime_profiles DROP CONSTRAINT %I',
                    existing
                );
            END IF;
        END $$;
        """
    )
    op.create_foreign_key(
        _PROFILE_USER_FK,
        "agent_runtime_profiles",
        "users",
        ["user_id"],
        ["id"],
        ondelete=ondelete,
    )


def _uppercase_statuses() -> None:
    for table, column in _UPPERCASE_STATUSES:
        op.execute(
            f"UPDATE {table} SET {column} = upper({column}) "
            f"WHERE {column} <> upper({column})"
        )


def downgrade() -> None:
    # Restores the schema. The data repairs are one-way and stay done: the
    # previous release reads capitalized statuses, a single default mark, and
    # profiles without the removed key or orphans exactly as it read them
    # before; the unbound Telegram senders and cleared handles were never
    # proof of anybody, so there is nothing true to put back.
    _replace_profile_user_fk(ondelete="SET NULL")
    op.drop_index(_ORG_DEFAULT_INDEX, table_name="agent_runtime_profiles")
    op.drop_column("agent_runtime_profiles", "version")

    op.drop_constraint(_FLOW_CAP_CHECK, "workflow_flows", type_="check")
    op.drop_column("workflow_flows", "max_concurrent_runs")

    op.create_index(
        "ix_users_telegram_username", "users", ["telegram_username"], unique=False
    )

    op.drop_column("agent_host_run_leases", "credential_expires_at")
    op.drop_column("agent_host_run_leases", "consumed_stream_id")
