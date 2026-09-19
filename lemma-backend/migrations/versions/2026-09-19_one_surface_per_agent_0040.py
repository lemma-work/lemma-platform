"""One pooled number per agent.

The WhatsApp numbers are about to come from a pool -- a handful at first, more
later -- and each surface that uses one takes one. Nothing stopped a single
agent from holding two of those surfaces and quietly taking two numbers out of
a scarce pool, so this makes "one agent, one pooled number" something the
database keeps rather than something the allocation code has to remember.

Deliberately narrower than "one surface per agent per platform", which is what
it looks like it should be. That broader rule contradicts two arrangements the
product already supports and tests:

- One agent can be reachable in **several Slack workspaces**, as SYSTEM
  surfaces that differ only by `external_workspace_id`; routing narrows by team
  id (`test_slack_workspace_narrowing_e2e`).
- One agent can hold a **system bot and a customer's own bot** on the same
  platform at once, and their threads are kept apart on purpose
  (`test_custom_bot_scope_and_system_bot_threads_do_not_cross`).

Neither of those spends a pooled number twice, so neither is what the pool
needs protecting from. A CUSTOM WhatsApp surface is the customer's own number
and is excluded for the same reason.

Revision ID: 0040_one_surface_per_agent
Revises: 0039_chat_onboarding
"""

from alembic import op
import sqlalchemy as sa

revision = "0040_one_surface_per_agent"
down_revision = "0039_chat_onboarding"
branch_labels = None
depends_on = None

_INDEX = "uq_agent_pooled_whatsapp_number"
_SCOPE = "surface_type = 'WHATSAPP' AND credential_mode = 'SYSTEM'"


def upgrade() -> None:
    # Checked before the index is built so a failure names the agents involved,
    # rather than arriving as a bare unique violation about one arbitrary row.
    # Which of two surfaces to keep is a decision for whoever owns the data.
    offenders = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT agent_id, count(*) AS rows FROM agent_surfaces "
                f"WHERE {_SCOPE} GROUP BY agent_id HAVING count(*) > 1 "
                "ORDER BY rows DESC"
            )
        )
        .fetchall()
    )
    if offenders:
        listed = ", ".join(
            f"agent {row.agent_id} holds {row.rows}" for row in offenders[:10]
        )
        raise RuntimeError(
            "Cannot enforce one pooled WhatsApp number per agent while "
            f"duplicates exist: {listed}"
            + (f" (and {len(offenders) - 10} more)" if len(offenders) > 10 else "")
            + ". Delete the surfaces that should not have been created, then "
            "run this migration again."
        )
    op.create_index(
        _INDEX,
        "agent_surfaces",
        ["agent_id"],
        unique=True,
        postgresql_where=sa.text(_SCOPE),
    )


def downgrade() -> None:
    op.drop_index(_INDEX, table_name="agent_surfaces")
