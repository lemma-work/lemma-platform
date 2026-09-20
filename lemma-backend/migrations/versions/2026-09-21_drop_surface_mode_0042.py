"""Drop ``agent_surfaces.mode``: it answered a question the platform already answers.

``mode`` was a two-member enum (``DM``/``EMAIL``) that was *derived* from
``surface_type`` on the way in, *validated* against it, and then read back to
re-derive the same answer. `AgentSurfaceEntity._resolve_mode` returned
``EMAIL if surface_type.is_email else DM``, and `_validate_binding` raised if a
caller supplied a combination that contradicted it -- a rule guarding a state
nothing could produce, because **no API schema ever carried the field**.
``SurfaceCreateRequest`` sets ``extra="forbid"``, so the setup guide that told
an operator to "POST the surface with platform and mode=DM" was documenting a
422. That text is fixed in this change too.

It had already misled the codebase once, and `ThreadShape`'s docstring records
it: *"asking ``surface.mode`` got it wrong: a channel thread inherited the DM
reset"* -- because one Slack install has both thread shapes at once and a column
on the surface cannot say which one a conversation is.

Its four production readers now ask `surface_type.is_email`, which is what they
meant. Nothing reads the column after this.

``event_mode`` keeps its column and loses its enum. The enum had one member,
``WEBHOOK``, and the two places that compared against it were tautologies. But
the column still does one real job: a row holding ``COMPOSIO_TRIGGER`` -- a
polled mailbox, retired with the Composio surfaces -- must read as *absent*
rather than as a webhook surface, and no migration deletes those rows on purpose
(they are configuration somebody chose). That check is now an allow-list against
a named constant, which also means an unexpected value reads as absent instead
of being silently accepted.

Reversible: ``downgrade`` restores the column with its original type, default
and NOT NULL, and backfills the derived value, so a rolled-back deployment sees
exactly what it wrote.

Revision ID: 0042_drop_surface_mode
Revises: 0041_surface_indexes
"""

from alembic import op
import sqlalchemy as sa

revision = "0042_drop_surface_mode"
down_revision = "0041_surface_indexes"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_column("agent_surfaces", "mode")


def downgrade() -> None:
    op.add_column(
        "agent_surfaces",
        sa.Column(
            "mode",
            sa.String(length=50),
            nullable=False,
            server_default="DM",
        ),
    )
    # The value it always held: derived from the platform, never from a caller.
    op.execute("UPDATE agent_surfaces SET mode = 'EMAIL' WHERE surface_type = 'RESEND'")
