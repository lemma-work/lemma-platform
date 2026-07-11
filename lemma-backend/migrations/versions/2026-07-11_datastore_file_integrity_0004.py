"""Add a durable content identity to datastore files.

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
        sa.Column("content_sha256", sa.String(length=64), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("datastore_files", "content_sha256")
