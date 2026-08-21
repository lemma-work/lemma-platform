"""Add immutable function artifacts and one-run execution state.

Revision ID: 0008_function_execution
Revises: 0007_auth_hardening
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0008_function_execution"
down_revision = "0007_auth_hardening"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.alter_column(
        "function_runs",
        "started_at",
        type_=sa.DateTime(timezone=True),
        postgresql_using="started_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "function_runs",
        "completed_at",
        type_=sa.DateTime(timezone=True),
        postgresql_using="completed_at AT TIME ZONE 'UTC'",
    )

    op.drop_column("functions", "python_packages")
    op.drop_column("functions", "code_hash")
    op.add_column(
        "functions",
        sa.Column("revision_hash", sa.String(length=71), nullable=True),
    )

    op.drop_column("function_runs", "workspace_process_id")
    op.drop_column("function_runs", "workspace_session_id")
    op.add_column(
        "function_runs",
        sa.Column("revision_hash", sa.String(length=71), nullable=True),
    )
    op.add_column(
        "function_runs",
        sa.Column("deadline_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("function_runs", "deadline_at")
    op.drop_column("function_runs", "revision_hash")
    op.add_column(
        "function_runs",
        sa.Column("workspace_session_id", sa.String(), nullable=True),
    )
    op.add_column(
        "function_runs",
        sa.Column("workspace_process_id", sa.String(), nullable=True),
    )

    op.drop_column("functions", "revision_hash")
    op.add_column(
        "functions",
        sa.Column("code_hash", sa.String(), nullable=True),
    )
    op.add_column(
        "functions",
        sa.Column(
            "python_packages",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
    )

    op.alter_column(
        "function_runs",
        "completed_at",
        type_=sa.DateTime(timezone=False),
        postgresql_using="completed_at AT TIME ZONE 'UTC'",
    )
    op.alter_column(
        "function_runs",
        "started_at",
        type_=sa.DateTime(timezone=False),
        postgresql_using="started_at AT TIME ZONE 'UTC'",
    )
