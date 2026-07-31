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
    op.execute(
        sa.text(
            """
            DO $$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM agents
                    WHERE name IN ('POD_DEFAULT', 'pod_default')
                ) THEN
                    RAISE EXCEPTION
                        'POD_DEFAULT and pod_default are reserved agent names; rename existing agents before upgrading';
                END IF;
            END
            $$;
            """
        )
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_agent_conv_user_pod_roots",
            "agent_conversations",
            ["user_id", "pod_id", "id"],
            unique=False,
            postgresql_where=sa.text("parent_id IS NULL"),
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_agent_conv_user_pod_agent_roots",
            "agent_conversations",
            [
                "user_id",
                "pod_id",
                sa.text(
                    "COALESCE(agent_id, "
                    "'00000000-0000-0000-0000-000000000001'::uuid)"
                ),
                "id",
            ],
            unique=False,
            postgresql_where=sa.text("parent_id IS NULL"),
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_agent_conv_pod_assistant_roots",
            table_name="agent_conversations",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_agent_conv_pod_agent_roots",
            table_name="agent_conversations",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_agent_conv_parent",
            table_name="agent_conversations",
            postgresql_concurrently=True,
        )
    op.create_check_constraint(
        "ck_agents_name_not_pod_default_selector",
        "agents",
        "name NOT IN ('POD_DEFAULT', 'pod_default')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_agents_name_not_pod_default_selector",
        "agents",
        type_="check",
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_agent_conv_pod_assistant_roots",
            "agent_conversations",
            ["user_id", "agent_id", "pod_id", "parent_id", "id"],
            unique=False,
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_agent_conv_pod_agent_roots",
            "agent_conversations",
            ["pod_id", "agent_id", "user_id", "parent_id", "id"],
            unique=False,
            postgresql_concurrently=True,
        )
        op.create_index(
            "ix_agent_conv_parent",
            "agent_conversations",
            ["parent_id"],
            unique=False,
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_agent_conv_user_pod_roots",
            table_name="agent_conversations",
            postgresql_concurrently=True,
        )
        op.drop_index(
            "ix_agent_conv_user_pod_agent_roots",
            table_name="agent_conversations",
            postgresql_concurrently=True,
        )
