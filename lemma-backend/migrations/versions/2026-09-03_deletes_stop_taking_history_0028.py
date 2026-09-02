"""Stop a deleted target taking the automation and the audit trail with it.

Three foreign keys said ``ON DELETE CASCADE`` where the row on the far side is
not part of the thing being deleted:

``schedules.workflow_id`` and ``schedules.agent_id``. Deleting a workflow
deleted every schedule pointing at it, and ``schedule_runs.schedule_id``
cascades from ``schedules``, so the firing history went too. Restructuring a
workflow by deleting and recreating it -- the normal way to do it -- silently
removed the automation that ran it and every record that it had ever run. The
same table already takes the opposite care one column over: ``account_id`` and
``connector_trigger_id`` are ``SET NULL`` with comments explaining why, so this
reads as an oversight rather than a decision.

``function_runs.function_id``. Deleting a function deleted every run it had
ever had -- inputs, outputs, logs, errors -- and any run still in flight
vanished mid-execution, leaving whatever was waiting on it to time out. The
docs promise the opposite: a deleted function stops being runnable and keeps
the history of what it did.

``SET NULL`` in all three places, matching the sibling columns. A schedule with
no target survives as a row a person can see and repoint; the fire path records
the firing as failed and says the target is missing rather than dropping it.
A run with no function keeps everything it recorded about that execution.

``function_runs.function_id`` has to become nullable for its ``SET NULL`` to be
legal. No endpoint can reach an orphaned run -- every run route resolves the
function by name first -- so nothing on the wire starts seeing a null; the rows
are audit history, and the retention sweep still ages them out on schedule.

There is deliberately no data migration. Rows destroyed by the old cascades are
gone, and inventing replacements would be worse than the gap.

Revision ID: 0028_deletes_stop_taking_history
Revises: 0027_schedule_account_set_null
"""

import sqlalchemy as sa
from alembic import op

revision = "0028_deletes_stop_taking_history"
down_revision = "0027_schedule_account_set_null"
branch_labels = None
depends_on = None

_SCHEDULE_WORKFLOW_FK = "schedules_workflow_id_fkey"
_SCHEDULE_AGENT_FK = "schedules_agent_id_fkey"
_FUNCTION_RUN_FK = "function_runs_function_id_fkey"


def _repoint(constraint: str, table: str, referent: str, column: str, on_delete: str):
    op.drop_constraint(constraint, table, type_="foreignkey")
    op.create_foreign_key(
        constraint,
        table,
        referent,
        [column],
        ["id"],
        ondelete=on_delete,
    )


def upgrade() -> None:
    _repoint(
        _SCHEDULE_WORKFLOW_FK, "schedules", "workflow_flows", "workflow_id", "SET NULL"
    )
    _repoint(_SCHEDULE_AGENT_FK, "schedules", "agents", "agent_id", "SET NULL")

    op.alter_column(
        "function_runs",
        "function_id",
        existing_type=sa.Uuid(),
        nullable=True,
    )
    _repoint(_FUNCTION_RUN_FK, "function_runs", "functions", "function_id", "SET NULL")


def downgrade() -> None:
    _repoint(
        _SCHEDULE_WORKFLOW_FK, "schedules", "workflow_flows", "workflow_id", "CASCADE"
    )
    _repoint(_SCHEDULE_AGENT_FK, "schedules", "agents", "agent_id", "CASCADE")

    _repoint(_FUNCTION_RUN_FK, "function_runs", "functions", "function_id", "CASCADE")
    # Runs orphaned while the new behaviour was live have no function to point
    # back at, and the column is about to stop accepting null. Removing them is
    # what the old cascade would have done to them anyway.
    op.execute(sa.text("DELETE FROM function_runs WHERE function_id IS NULL"))
    op.alter_column(
        "function_runs",
        "function_id",
        existing_type=sa.Uuid(),
        nullable=False,
    )
