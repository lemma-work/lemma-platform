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
pod, whichever of its bots it writes to. `last_inbound_at` is when they last
wrote from it (WhatsApp lets a business write freely only within a day of it),
and `unsubscribed_at` when they asked not to be written to there. Both tables
cascade from the pod, and
identities from their contact, so forgetting a contact forgets their handles.

**`usage_contacts_caps`** is what an organization lets its bots spend answering
contacts in a month, set by an organization owner. Contacts are never billed;
this is the ceiling on what they can cost. No row means the deployment's
default cap; a row with no limit means an owner removed it. It goes with its
organization.

**`agent_surface_conversation_links`** gains an index led by `external_user_id`:
a contact's conversation is found by its `~contact:{id}` link alone, whichever
bot and thread it is on. **`notifications.asked_in_private`** marks a question
passed on from a contact's or a web visitor's private chat, so the member is
not told it was asked in a group.

**`datastore_tables.contact_owned`** marks a table whose rows the pod keeps about
its contacts: it carries a `contact_id`, every member sees every row, and a
contact's run reads only rows naming that contact, under a row-level policy
installed when the flag is set. **`functions.contacts_invoke`** marks a function
a contact's conversation may call. Both default to false, so nothing existing
changes. A run such a function makes for a contact acts for no member:
**`function_runs.user_id`** becomes nullable and **`function_runs.contact_id`**
names the contact it served, cascading from the contact.

**`agent_surface_web_widgets`** is a pod's chat for other people's web pages,
each answering as one of its agents. A widget is also how a page's visitor is
known: the session it opens is what adds rows to a table open to visitors. Its `public_key` is
unique and readable by anybody; its `signing_secret` (encrypted) signs the
tokens a customer's server issues for its own signed-in users.
**`visitor_sessions`** is one visitor's hold on one widget, found by a secret
stored only as a digest and exchanged for short-lived access tokens. `strength`
is how they are known (`ANONYMOUS`, `CODE`, `HOST`), `expires_at` when the
session ends and `revoked_at` when it was ended early. It cascades from its
conversation, so forgetting a contact ends the sessions that led to them.
**`agent_surface_web_codes`** is the one-time codes sent to email addresses
visitors typed, stored as salted digests.

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

_VISITOR_SESSION_INDEXES = (
    ("ix_visitor_sessions_pod", "pod_id"),
    ("ix_visitor_sessions_widget", "widget_id"),
    ("ix_visitor_sessions_contact", "contact_id"),
    ("ix_visitor_sessions_conversation", "conversation_id"),
    ("ix_visitor_sessions_expires", "expires_at"),
)


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
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("unsubscribed_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column(
            "organization_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("organizations.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("monthly_limit_usd", sa.Numeric(24, 9), nullable=True),
        sa.Column("updated_by_user_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        "uq_usage_contacts_cap_org",
        "usage_contacts_caps",
        ["organization_id"],
        unique=True,
    )

    op.create_table(
        "agent_surface_web_widgets",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "pod_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "agent_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agents.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("public_key", sa.String(64), nullable=False),
        sa.Column("signing_secret", sa.Text(), nullable=False),
        sa.Column("allowed_origins", postgresql.JSONB(), nullable=False),
        sa.Column("answer", sa.String(20), nullable=False),
        sa.Column(
            "looked_after_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index(
        "uq_web_widget_pod_name",
        "agent_surface_web_widgets",
        ["pod_id", "name"],
        unique=True,
    )
    op.create_index(
        "uq_web_widget_public_key",
        "agent_surface_web_widgets",
        ["public_key"],
        unique=True,
    )
    # -- web widgets' visitors: visitor_sessions and their codes -------------
    op.create_index("ix_web_widget_agent", "agent_surface_web_widgets", ["agent_id"])
    op.create_index(
        "ix_web_widget_looked_after_by",
        "agent_surface_web_widgets",
        ["looked_after_by"],
    )
    op.create_table(
        "visitor_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "pod_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("pods.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "widget_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_surface_web_widgets.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "contact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("contacts.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("strength", sa.String(20), nullable=False),
        sa.Column(
            "conversation_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("agent_conversations.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("secret_hash", sa.String(64), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("ip_hash", sa.String(64), nullable=True),
    )
    op.create_index(
        "uq_visitor_sessions_secret",
        "visitor_sessions",
        ["secret_hash"],
        unique=True,
    )
    for name, column in _VISITOR_SESSION_INDEXES:
        op.create_index(name, "visitor_sessions", [column])
    op.create_table(
        "agent_surface_web_codes",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "session_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("visitor_sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("code_hash", sa.String(64), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("consumed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_web_code_session",
        "agent_surface_web_codes",
        ["session_id", "email"],
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
    _upgrade_contact_function_runs()
    _upgrade_contact_links_and_questions()


def _upgrade_contact_function_runs() -> None:
    """A run started for a contact acts for no member and names the contact."""
    op.alter_column("function_runs", "user_id", nullable=True)
    op.add_column(
        "function_runs",
        sa.Column(
            "contact_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "contacts.id",
                name="fk_function_runs_contact_id",
                ondelete="CASCADE",
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_function_runs_contact_id",
        "function_runs",
        ["contact_id"],
        postgresql_where=sa.text("contact_id IS NOT NULL"),
    )


def _downgrade_contact_function_runs() -> None:
    # A run with no member cannot be kept once ``user_id`` is required again.
    op.execute("DELETE FROM function_runs WHERE user_id IS NULL")
    op.drop_index("ix_function_runs_contact_id", table_name="function_runs")
    op.drop_column("function_runs", "contact_id")
    op.alter_column("function_runs", "user_id", nullable=False)


def _upgrade_contact_links_and_questions() -> None:
    """Finding a contact's conversation by its link, and private-chat questions.

    The index is declared here only, not on the link model: autogenerating a
    migration from the models would propose dropping it, and must not.
    """
    op.create_index(
        "ix_agent_surface_link_external_user",
        "agent_surface_conversation_links",
        ["external_user_id", "updated_at"],
    )
    op.add_column(
        "notifications",
        sa.Column(
            "asked_in_private",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def _downgrade_contact_links_and_questions() -> None:
    op.drop_column("notifications", "asked_in_private")
    op.drop_index(
        "ix_agent_surface_link_external_user",
        table_name="agent_surface_conversation_links",
    )


def downgrade() -> None:
    _downgrade_contact_links_and_questions()
    _downgrade_contact_function_runs()
    op.drop_index("ix_web_code_session", table_name="agent_surface_web_codes")
    op.drop_table("agent_surface_web_codes")
    for name, _column in reversed(_VISITOR_SESSION_INDEXES):
        op.drop_index(name, table_name="visitor_sessions")
    op.drop_index("uq_visitor_sessions_secret", table_name="visitor_sessions")
    op.drop_table("visitor_sessions")
    op.drop_index(
        "ix_web_widget_looked_after_by", table_name="agent_surface_web_widgets"
    )
    op.drop_index("ix_web_widget_agent", table_name="agent_surface_web_widgets")
    op.drop_index("uq_web_widget_public_key", table_name="agent_surface_web_widgets")
    op.drop_index("uq_web_widget_pod_name", table_name="agent_surface_web_widgets")
    op.drop_table("agent_surface_web_widgets")
    op.drop_column("functions", "contacts_invoke")
    op.drop_column("datastore_tables", "contact_owned")
    op.drop_index("uq_usage_contacts_cap_org", table_name="usage_contacts_caps")
    op.drop_table("usage_contacts_caps")
    op.drop_index("ix_contact_identities_contact", table_name="contact_identities")
    op.drop_table("contact_identities")
    op.drop_index("ix_contacts_pod_created", table_name="contacts")
    op.drop_table("contacts")
