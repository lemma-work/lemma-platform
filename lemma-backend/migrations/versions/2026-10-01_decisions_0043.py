"""Decisions: pod deciders, their versions, the decisions made, and examples.

Four new tables, none read by existing code, so this takes no lock on anything
already serving.

`deciders` holds a pod decider and a copy of its current definition;
`decider_versions` every definition ever saved, never edited, because a
decision names the version that answered it. `decisions` is unique per (pod,
decider, subject) when a subject is given -- NULLS NOT DISTINCT, so a system
decision asked outside any pod is asked once too -- which is what makes a
decision asked once. `decision_examples` is what people said answers were.

Revision ID: 0043_decisions
Revises: 0042_mcp_access
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0043_decisions"
down_revision = "0042_mcp_access"
branch_labels = None
depends_on = None


def _timestamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "deciders",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column(
            "visibility",
            sa.String(length=16),
            nullable=False,
            server_default=sa.text("'POD'"),
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        *_timestamps(),
        sa.UniqueConstraint("pod_id", "name", name="uq_deciders_pod_name"),
    )
    op.create_table(
        "decider_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "decider_id",
            sa.Uuid(),
            sa.ForeignKey("deciders.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "definition", postgresql.JSONB(astext_type=sa.Text()), nullable=False
        ),
        sa.Column(
            "created_by",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "decider_id", "version", name="uq_decider_versions_version"
        ),
    )
    op.create_table(
        "decisions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("organization_id", sa.Uuid(), nullable=True),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("visibility", sa.String(length=16), nullable=False),
        sa.Column("decider_scope", sa.String(length=16), nullable=False),
        sa.Column("decider_key", sa.String(length=128), nullable=False),
        sa.Column("decider_name", sa.String(length=64), nullable=True),
        sa.Column("decider_version", sa.Integer(), nullable=True),
        sa.Column("subject_key", sa.String(length=512), nullable=True),
        sa.Column("shape", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("answers", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("open", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("trace", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=True),
        sa.Column("evidence_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "answered_by_user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        *_timestamps(),
    )
    op.create_index(
        "uq_decisions_subject",
        "decisions",
        ["pod_id", "decider_key", "subject_key"],
        unique=True,
        postgresql_where=sa.text("subject_key IS NOT NULL"),
        postgresql_nulls_not_distinct=True,
    )
    op.create_index("ix_decisions_pod_created", "decisions", ["pod_id", "created_at"])
    op.create_index(
        "ix_decisions_evidence_expiry", "decisions", ["evidence_expires_at"]
    )
    op.create_table(
        "decision_examples",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "pod_id",
            sa.Uuid(),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("decider_key", sa.String(length=128), nullable=False),
        sa.Column("question_key", sa.String(length=64), nullable=False),
        sa.Column("value", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("evidence", sa.Text(), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "decision_id",
            sa.Uuid(),
            sa.ForeignKey("decisions.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_decision_examples_lookup",
        "decision_examples",
        ["pod_id", "decider_key", "question_key", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_decision_examples_lookup", table_name="decision_examples")
    op.drop_table("decision_examples")
    op.drop_index("ix_decisions_evidence_expiry", table_name="decisions")
    op.drop_index("ix_decisions_pod_created", table_name="decisions")
    op.drop_index("uq_decisions_subject", table_name="decisions")
    op.drop_table("decisions")
    op.drop_table("decider_versions")
    op.drop_table("deciders")
