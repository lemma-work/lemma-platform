"""Index unified pod conversation history.

Revision ID: 0006_conversation_history_index
Revises: 0005_identity_normalization
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op


revision = "0006_conversation_history_index"
down_revision = "0005_identity_normalization"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_index(
        "ix_agent_conv_user_pod_roots",
        "agent_conversations",
        ["user_id", "pod_id", "id"],
        unique=False,
        postgresql_where=sa.text("parent_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "ix_agent_conv_user_pod_roots",
        table_name="agent_conversations",
        postgresql_where=sa.text("parent_id IS NULL"),
    )
