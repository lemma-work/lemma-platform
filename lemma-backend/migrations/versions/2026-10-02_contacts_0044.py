"""Contacts: the people a pod's bots answer who are not members.

A bot has answered two kinds of people: the pod's members, as themselves, and
-- in a group that allows it -- people outside the pod, as nobody. A contact is
the third: somebody the pod knows by a handle a platform or a mail service
vouched for, answered in a private chat of their own.

**`contacts`** is one row per person per pod. A contact never signs in, never
joins the pod and holds no grant, so the row is only a name to address them by.

**`contact_identities`** is the handles a contact is known by -- a phone number,
an email address, a Telegram user id -- and how each was vouched for
(`strength`). `(pod_id, kind, value)` is unique: one number is one contact in a
pod, whichever of its bots it writes to. Both tables cascade from the pod, and
identities from their contact, so forgetting a contact forgets their handles.

**`usage_contacts_caps`** is what an organization lets its bots spend answering
contacts in a month, set by an organization admin. Contacts are never billed;
this is the ceiling on what they can cost. No row means no cap of the
organization's own.

**`datastore_tables.contact_owned`** marks a table whose rows the pod keeps about
its contacts: it carries a `contact_id`, every member sees every row, and a
contact's run reads only rows naming that contact, under a row-level policy
installed when the flag is set. **`functions.contacts_invoke`** marks a function
a contact's conversation may call. Both default to false, so nothing existing
changes.

The tables are new and nothing is backfilled: a contact becomes known the first
time they write to a bot that answers contacts.

Revision ID: 0044_contacts
Revises: 0043_surface_groups
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0044_contacts"
down_revision = "0043_surface_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "contacts",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "pod_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("display_name", sa.String(255), nullable=True),
    )
    op.create_index("ix_contacts_pod_created", "contacts", ["pod_id", "created_at"])

    op.create_table(
        "contact_identities",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "contact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "pod_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("value", sa.String(320), nullable=False),
        sa.Column("strength", sa.String(20), nullable=False),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "pod_id", "kind", "value", name="uq_contact_identities_pod_handle"
        ),
    )
    op.create_index(
        "ix_contact_identities_contact", "contact_identities", ["contact_id"]
    )

    op.create_table(
        "usage_contacts_caps",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("monthly_limit_usd", sa.Numeric(24, 9), nullable=True),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "uq_usage_contacts_cap_org",
        "usage_contacts_caps",
        ["organization_id"],
        unique=True,
    )

    op.add_column(
        "datastore_tables",
        sa.Column(
            "contact_owned",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.add_column(
        "functions",
        sa.Column(
            "contacts_invoke",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("functions", "contacts_invoke")
    op.drop_column("datastore_tables", "contact_owned")
    op.drop_index("uq_usage_contacts_cap_org", table_name="usage_contacts_caps")
    op.drop_table("usage_contacts_caps")
    op.drop_index("ix_contact_identities_contact", table_name="contact_identities")
    op.drop_table("contact_identities")
    op.drop_index("ix_contacts_pod_created", table_name="contacts")
    op.drop_table("contacts")
