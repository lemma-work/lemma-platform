"""Let an approval claim its execution before it runs the tool.

Approving a `request_approval` runs the wrapped tool with the *user's*
authority — deleting records, deploying an app, sending mail. It must happen at
most once, and it did not.

The guard was a read. `_reconcile_resume` called `get_tool_return(...)` and, if
nothing came back, built the return — and building it is what executes the tool.
The return is written only afterwards. So the window between the read and the
write was one whole tool execution wide, and nothing held a claim across it.

Concurrent clicks were in fact safe: the reconcile job has a deterministic id
and streaq's `publish_task` does `SET NX`, so the second click never enqueues.
What is not safe is a *retry of the job already claimed*. `reconcile_agent_approval`
carries `max_tries=3`, and streaq requeues a job twice over: once when a worker
shutting down cancels it inside the grace period (`worker.py`'s "cancelled, will
be retried" branch), and again when `xautoclaim` reclaims a stream entry whose
worker died. `execute_approved_tool_as_user` catches `Exception`, and a
`CancelledError` is not one, so nothing intercepted the unwind either. A worker
rolled thirty seconds into an approved forty-second command ran it a second time
on the retry.

`execution_claimed_at` moves the guard from a read to a conditional UPDATE that
Postgres arbitrates: `SET execution_claimed_at = now() WHERE ... AND
execution_claimed_at IS NULL RETURNING id`, committed before the tool runs. The
loser gets no row and skips the build.

Nullable with no backfill on purpose. Existing rows are decisions that have
already been reconciled — claiming them retroactively would be inventing a fact,
and leaving them NULL costs nothing: their tool return exists, so the earlier
guard still short-circuits before any claim is attempted.

Downgrade drops the column. That reinstates the race, which is the honest
meaning of downgrading past this.
"""

from alembic import op
import sqlalchemy as sa

revision = "0021_approval_execution_claim"
down_revision = "0020_schedule_run_last_inspected"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_approval_decisions",
        sa.Column("execution_claimed_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_approval_decisions", "execution_claimed_at")
