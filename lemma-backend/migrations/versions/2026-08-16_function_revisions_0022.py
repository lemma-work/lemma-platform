"""Index the function revisions that storage has been keeping all along.

Every code change builds an immutable artifact at ``artifacts/<hash>.zip`` and
writes its source to ``revisions/<hash>/function.py``. Both paths are
content-addressed and nothing deletes them, so the bytes of every revision a
function ever had are still on disk -- but the only record that a revision
existed was ``functions.revision_hash`` (the live one) and the ``revision_hash``
stamped on old run rows. There was no way to list what a function had been, let
alone go back to it.

This table is that index. It snapshots the schemas alongside the hash because
``input_schema`` / ``output_schema`` / ``config_schema`` live on the ``functions``
row: promoting an old revision has to restore its contract too, or the function
would advertise schemas that its code does not implement, and every agent and
workflow bound to it would be reading a lie.

The backfill records the CURRENT revision of each function and stops there.
Hashes that appear only on old ``function_runs`` rows are deliberately left
alone: their artifacts still exist, but their schemas do not, and a row invented
with today's schemas would be a guess presented as history.

Revision ID: 0022_function_revisions
Revises: 0021_app_release_history
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0022_function_revisions"
down_revision = "0021_app_release_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "function_revisions",
        sa.Column("id", sa.UUID(), primary_key=True),
        sa.Column(
            "function_id",
            sa.UUID(),
            sa.ForeignKey("functions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("revision_hash", sa.String(length=71), nullable=False),
        sa.Column("code_path", sa.String(), nullable=False),
        sa.Column("input_schema", postgresql.JSONB(), nullable=False),
        sa.Column("output_schema", postgresql.JSONB(), nullable=False),
        sa.Column("config_schema", postgresql.JSONB(), nullable=True),
        sa.Column(
            "created_by",
            sa.UUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("label", sa.String(), nullable=True),
        sa.Column("pruned_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("generation", sa.UUID(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "function_id", "revision_number", name="uq_function_revision_number"
        ),
    )
    op.create_index(
        "uq_function_revision_active_hash",
        "function_revisions",
        ["function_id", "revision_hash"],
        unique=True,
        postgresql_where=sa.text("pruned_at IS NULL"),
    )
    op.create_index(
        "ix_function_revision_function_created",
        "function_revisions",
        ["function_id", sa.text("created_at DESC")],
    )

    # Every function that has a built revision gets exactly one row: the one it
    # is running. `updated_at` is when that revision became live, which is the
    # closest honest answer to when it was built.
    op.execute(
        """
        INSERT INTO function_revisions (
            id, function_id, revision_number, revision_hash, code_path,
            input_schema, output_schema, config_schema, created_by,
            created_at
        )
        SELECT gen_random_uuid(),
               f.id,
               1,
               f.revision_hash,
               f.code_path,
               COALESCE(f.input_schema, '{}'::jsonb),
               COALESCE(f.output_schema, '{}'::jsonb),
               f.config_schema,
               f.user_id,
               COALESCE(f.updated_at, f.created_at, NOW())
        FROM functions AS f
        WHERE f.revision_hash IS NOT NULL
          AND f.code_path IS NOT NULL
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_function_revision_function_created", table_name="function_revisions"
    )
    op.drop_table("function_revisions")
