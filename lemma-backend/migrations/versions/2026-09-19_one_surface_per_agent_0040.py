"""One surface per agent per platform.

An agent reaches a platform in exactly one place: one Slack app, one WhatsApp
number, one Telegram bot. Nothing said so before, and the rule that was enforced
is a different one -- a connected account or Lemma-managed identity is claimable
once per organization -- which happens to permit an agent holding several.

It matters most for WhatsApp, where the numbers are about to come from a small
pool and each surface takes one: without this an agent could quietly hold two
of a scarce thing. It is the same rule everywhere else because the reason is the
same everywhere else -- two doors onto one platform for one agent is an
ambiguity, not a feature, and whoever is on the other side has no way to tell
which one they are talking to.

Two arrangements in the tree predate this rule and are being changed to match
it rather than exempted: surfaces for several Slack workspaces, and a system bot
running beside a customer's own bot. Both stay possible across *different*
agents, which is where they belong.

Revision ID: 0040_one_surface_per_agent
Revises: 0039_chat_onboarding
"""

from alembic import op
import sqlalchemy as sa

revision = "0040_one_surface_per_agent"
down_revision = "0039_chat_onboarding"
branch_labels = None
depends_on = None

_CONSTRAINT = "uq_agent_surface_agent_type"


def upgrade() -> None:
    # Checked before the index is built so a failure names the agents involved,
    # rather than arriving as a bare unique violation about one arbitrary row.
    # Which of two surfaces to keep is a decision for whoever owns the data.
    offenders = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT agent_id, surface_type, count(*) AS rows "
                "FROM agent_surfaces GROUP BY agent_id, surface_type "
                "HAVING count(*) > 1 ORDER BY rows DESC"
            )
        )
        .fetchall()
    )
    if offenders:
        listed = ", ".join(
            f"agent {row.agent_id} holds {row.rows} {row.surface_type} surfaces"
            for row in offenders[:10]
        )
        raise RuntimeError(
            "Cannot enforce one surface per agent per platform while "
            f"duplicates exist: {listed}"
            + (f" (and {len(offenders) - 10} more)" if len(offenders) > 10 else "")
            + ". Delete the surfaces that should not have been created, then "
            "run this migration again."
        )
    op.create_unique_constraint(
        _CONSTRAINT, "agent_surfaces", ["agent_id", "surface_type"]
    )


def downgrade() -> None:
    op.drop_constraint(_CONSTRAINT, "agent_surfaces", type_="unique")
