"""Every outsider is a contact, and a conversation names the contact it answers.

**`agent_conversations.contact_id`** names the contact a conversation answers,
cascading from the contact: forgetting them forgets what was said with them.
Only the server writes it.

**`contacts.merged_into_id`** points a contact that turned out to be another at
the one it became, and cascades: forgetting a contact forgets the contacts that
became it. **`contacts.answered_at`** is when an agent first answered them,
which the organization's contact limit counts.
**`usage_contacts_caps.max_contacts`** is how many contacts an organization's
bots may answer at all; null for no limit. `max_contacts_set` and
`monthly_limit_set` say which half of the cap an owner chose: a half they did
not follows the deployment's default. A cap stored before this was an owner's
spend choice.

Schema only. Contacts, groups and web chat were never released -- the pages
are behind feature flags and a bot answers no contact until an owner turns it
on -- so no deployment holds a contact's conversation keyed the old way
(``audience``/``contact_id`` in metadata), and there is nothing to carry over.

Revision ID: 0057_contacts_for_everyone
Revises: 0056_agent_surface_bundle_work
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision = "0057_contacts_for_everyone"
down_revision = "0056_agent_surface_bundle_work"
branch_labels = None
depends_on = None


def _repair_invalid(name: str) -> None:
    """Drop a half-built index so ``IF NOT EXISTS`` stops mistaking it for done."""
    half_built = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": name},
        )
        .scalar()
    )
    if half_built:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column(
            "merged_into_id",
            UUID(as_uuid=True),
            # Forgetting a contact forgets the contacts that became it: set to
            # null instead, they would come back as empty contacts.
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        if_not_exists=True,
    )
    op.add_column(
        "contacts",
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        if_not_exists=True,
    )
    op.add_column(
        "usage_contacts_caps",
        sa.Column("max_contacts", sa.Integer(), nullable=True),
        if_not_exists=True,
    )
    op.add_column(
        "usage_contacts_caps",
        sa.Column(
            "max_contacts_set",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        if_not_exists=True,
    )
    op.add_column(
        "usage_contacts_caps",
        sa.Column(
            "monthly_limit_set",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
        if_not_exists=True,
    )
    op.add_column(
        "agent_conversations",
        sa.Column(
            "contact_id",
            UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=True,
        ),
        if_not_exists=True,
    )
    op.create_index(
        "ix_contacts_merged_into",
        "contacts",
        ["merged_into_id"],
        if_not_exists=True,
    )
    # The organization's contact limit counts these before every outside turn.
    op.create_index(
        "ix_contacts_answered",
        "contacts",
        ["pod_id"],
        postgresql_where=sa.text("answered_at IS NOT NULL AND merged_into_id IS NULL"),
        if_not_exists=True,
    )
    # Concurrently, outside the transaction: conversations are written on
    # every turn, and a plain build would block those writers.
    with op.get_context().autocommit_block():
        _repair_invalid("ix_agent_conversations_contact_id")
        op.create_index(
            "ix_agent_conversations_contact_id",
            "agent_conversations",
            ["contact_id"],
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_agent_conversations_contact_id",
            table_name="agent_conversations",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_column("agent_conversations", "contact_id")
    op.drop_column("usage_contacts_caps", "monthly_limit_set")
    op.drop_column("usage_contacts_caps", "max_contacts_set")
    op.drop_column("usage_contacts_caps", "max_contacts")
    op.drop_index("ix_contacts_answered", table_name="contacts", if_exists=True)
    op.drop_index("ix_contacts_merged_into", table_name="contacts", if_exists=True)
    op.drop_column("contacts", "answered_at")
    op.drop_column("contacts", "merged_into_id")
