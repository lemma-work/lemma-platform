"""Drop the saved-site-login tables. The browser keeps its own session now.

``0035`` introduced ``web_logins`` and ``web_login_audit`` to hold a person's
site sessions: read the cookies out of a sandbox browser, decide which of them
constituted "a login", encrypt that, and rebuild it in a different browser on
the next run.

Both halves of that were guesses, and both were wrong in production.
``looks_signed_in`` accepted any local-storage entry for the origin -- so a
consent flag the agent itself had written by dismissing a cookie banner was
stored and reported to the person as their login being kept. ``_site_accepted``
then validated a restored session by opening the site's *root*, which on a
deployment whose root is a marketing page never looks like a login wall, so a
dead session passed the check and the run looped: "signed in with a saved
login", then a login form, again and again.

The sandbox's home became durable, so the fix is to stop reconstructing
anything. Chrome keeps its profile in ``/home/user/.lemma/browser/profile``,
which survives a suspend, and a person who signs in stays signed in the way
they do on their own machine. Listing and forgetting read and change that
browser directly, so there is nothing left for these tables to hold.

Dropped outright rather than migrated: the feature only ever ran in dev, the
rows are encrypted session cookies with no value once the mechanism reading
them is gone, and a session nobody can restore is not worth keeping. Anyone
who had signed in signs in once more.

``downgrade`` recreates the shape but not the rows, which is the honest
position -- the secrets were encrypted with a key this migration does not
have, and inventing empty rows would restore a table that claims logins exist.
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = "0036_drop_saved_site_logins"
down_revision = "0035_saved_site_logins"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_web_login_audit_user_created", table_name="web_login_audit")
    op.drop_table("web_login_audit")
    op.drop_index("ix_web_logins_user_id", table_name="web_logins")
    op.drop_table("web_logins")


def downgrade() -> None:
    op.create_table(
        "web_logins",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("origin", sa.String(length=255), nullable=False),
        sa.Column(
            "status",
            sa.String(length=16),
            nullable=False,
            server_default="ACTIVE",
        ),
        sa.Column("secret", postgresql.JSONB(), nullable=False),
        sa.Column("last_used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("user_id", "origin", name="uq_web_logins_user_origin"),
    )
    op.create_index("ix_web_logins_user_id", "web_logins", ["user_id"])
    op.create_table(
        "web_login_audit",
        sa.Column("id", sa.Uuid(), primary_key=True, nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("conversation_id", sa.Uuid(), nullable=True),
        sa.Column("origin", sa.String(length=255), nullable=False),
        sa.Column("action", sa.String(length=32), nullable=False),
        sa.Column("outcome", sa.String(length=32), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_web_login_audit_user_created",
        "web_login_audit",
        ["user_id", "created_at"],
    )
