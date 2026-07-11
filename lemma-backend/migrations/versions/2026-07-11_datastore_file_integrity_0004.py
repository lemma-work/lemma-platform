"""Add immutable-original and processing-phase metadata to datastore files.

Revision ID: 0004_datastore_file_integrity
Revises: 0003_backend_reliability
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision = "0004_datastore_file_integrity"
down_revision = "0003_backend_reliability"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "datastore_files",
        sa.Column("storage_key", sa.String(length=1536), nullable=True),
    )
    op.add_column(
        "datastore_files",
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "datastore_files",
        sa.Column(
            "content_revision",
            sa.Integer(),
            server_default="1",
            nullable=False,
        ),
    )
    op.add_column(
        "datastore_files",
        sa.Column("processing_phase", sa.String(length=32), nullable=True),
    )
    op.add_column(
        "datastore_files",
        sa.Column("processing_started_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Preserve the exact legacy object location for existing rows. New uploads
    # use immutable revisioned keys, while old rows remain readable without an
    # object-store migration.
    op.execute(
        """
        UPDATE datastore_files
        SET storage_key =
            'pods/' || pod_id::text || '/files/' || ltrim(path, '/')
        WHERE kind = 'FILE' AND storage_key IS NULL
        """
    )
    op.create_index(
        "ix_datastore_file_storage_key",
        "datastore_files",
        ["storage_key"],
        unique=True,
        postgresql_where=sa.text("storage_key IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_datastore_file_storage_key", table_name="datastore_files")
    op.drop_column("datastore_files", "processing_started_at")
    op.drop_column("datastore_files", "processing_phase")
    op.drop_column("datastore_files", "content_revision")
    op.drop_column("datastore_files", "content_sha256")
    op.drop_column("datastore_files", "storage_key")
