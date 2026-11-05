"""Every outsider is a contact, and a conversation names the contact it answers.

**`agent_conversations.contact_id`** names the contact a conversation answers,
cascading from the contact: forgetting them forgets what was said with them.
Only the server writes it.

**`contacts.merged_into_id`** points a contact that turned out to be another at
the one it became, and cascades: forgetting a contact forgets the contacts that
became it. **`contacts.answered_at`** is when an agent first answered them,
which the organization's contact limit counts.
**`contact_suppressions`** are handles nobody in the pod may write to -- they
said stop, unsubscribed, or bounced -- keyed by a hash of the handle so the
opt-out outlives the contact being forgotten.
Any handle marked unsubscribed on its row becomes a suppression, and the
row's `unsubscribed_at` goes: an opt-out lives only in the table that outlives
the contact.
**`contact_identities`** are spelled one way per handle: phone numbers become
E.164 (`+447700900123`, from WhatsApp's bare digits), and a host identity
names the product that vouched for it, `host:{issuer}:{sub}` (`default` for
every widget so far, recorded in the new `issuer`). `value_raw` keeps the
handle as it arrived.
**`agent_surface_web_widgets.host_issuer`** is the product a widget's host
tokens speak for.
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


def _respell_identities() -> None:
    """Every handle in the one spelling it is looked up and suppressed under,
    and every opt-out carried into ``contact_suppressions`` by its hash."""
    op.add_column(
        "contact_identities",
        sa.Column("issuer", sa.String(64), nullable=True),
        if_not_exists=True,
    )
    op.add_column(
        "contact_identities",
        sa.Column("value_raw", sa.String(320), nullable=True),
        if_not_exists=True,
    )
    op.execute(
        "UPDATE contact_identities SET value_raw = value WHERE value_raw IS NULL"
    )
    op.execute(
        "UPDATE contact_identities SET value = '+' || value "
        "WHERE kind = 'PHONE' AND value ~ '^[0-9]+$'"
    )
    op.execute(
        "UPDATE contact_identities SET value = 'host:default:' || substr(value, 6), "
        "issuer = 'default' WHERE kind = 'HOST' AND issuer IS NULL"
    )
    op.execute(
        "INSERT INTO contact_suppressions (pod_id, kind, value_hash, reason) "
        "SELECT pod_id, kind, sha256(convert_to(value, 'UTF8')), 'unsubscribe' "
        "FROM contact_identities WHERE unsubscribed_at IS NOT NULL "
        "ON CONFLICT DO NOTHING"
    )
    op.drop_column("contact_identities", "unsubscribed_at", if_exists=True)
    op.add_column(
        "agent_surface_web_widgets",
        sa.Column(
            "host_issuer",
            sa.String(64),
            nullable=False,
            server_default=sa.text("'default'"),
        ),
        if_not_exists=True,
    )


def _spell_identities_back() -> None:
    op.drop_column("agent_surface_web_widgets", "host_issuer", if_exists=True)
    op.add_column(
        "contact_identities",
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
        if_not_exists=True,
    )
    op.execute(
        "UPDATE contact_identities AS identity SET unsubscribed_at = s.created_at "
        "FROM contact_suppressions AS s WHERE s.pod_id = identity.pod_id "
        "AND s.kind = identity.kind "
        "AND s.value_hash = sha256(convert_to(identity.value, 'UTF8'))"
    )
    op.execute(
        "UPDATE contact_identities SET value = 'host:' || substr(value, 7 + "
        "length(issuer)) WHERE kind = 'HOST' AND issuer IS NOT NULL"
    )
    op.execute(
        "UPDATE contact_identities SET value = ltrim(value, '+') WHERE kind = 'PHONE'"
    )
    op.drop_column("contact_identities", "value_raw", if_exists=True)
    op.drop_column("contact_identities", "issuer", if_exists=True)


def upgrade() -> None:
    op.create_table(
        "contact_suppressions",
        sa.Column(
            "pod_id",
            UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("kind", sa.String(20), primary_key=True),
        sa.Column("value_hash", sa.LargeBinary(32), primary_key=True),
        sa.Column("reason", sa.String(20), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        if_not_exists=True,
    )
    _respell_identities()
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
    _spell_identities_back()
    op.drop_table("contact_suppressions", if_exists=True)
