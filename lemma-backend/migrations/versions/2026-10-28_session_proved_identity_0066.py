"""A Telegram account proved by its owner signing in to Lemma.

A Telegram sender was recognised only by a phone number -- their own contact,
shared with the bot, matching a verified number on their Lemma profile -- or
by the shared bot's email code. A member with no verified number, talking to a
pod's own bot, could not be recognised at all: in a group they ran as a
stranger. They can now prove the account the other way round: a one-time code
minted in their signed-in Lemma session, opened in Telegram from that account,
and confirmed back in the same session.

``session_proved_at`` says the binding stands on that proof, so it holds
without a phone and survives a phone change; ``proved_through_surface_id`` is
the bot it was proved through. Both nullable, nothing backfilled: every
existing row stands on what it stood on before.

Revision ID: 0066_session_proved_identity
Revises: 0065_schedule_reset_and_author
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0066_session_proved_identity"
down_revision = "0065_schedule_reset_and_author"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "surface_verified_identities",
        sa.Column("session_proved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "surface_verified_identities",
        sa.Column(
            "proved_through_surface_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "agent_surfaces.id",
                ondelete="SET NULL",
                name="fk_surface_identity_proved_through_surface",
            ),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("surface_verified_identities", "proved_through_surface_id")
    op.drop_column("surface_verified_identities", "session_proved_at")
