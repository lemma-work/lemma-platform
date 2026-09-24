"""The installation owner: one row, naming the first account on a Desktop install.

A Lemma Desktop installation is one person's computer, and the next change
gives that person -- and only that person -- the ability to run commands on
the host rather than in the VM. That needs a durable answer to "who is the
owner?", recorded once and never re-decided by whoever signs up next.

**One row, by construction.** The primary key is a boolean that a check
constraint pins to true. Two concurrent first signups can both try to insert
it; the database accepts one and refuses the other, so ownership is never
settled by a read-then-write that two requests can both pass.

**A reservation before an account.** The row is written *before* SuperTokens
creates the user, with `user_id` null, and bound to the user when the local row
is created. Taking it after the account exists would let two simultaneous first
signups both be admitted as "the first" under invite-only -- the loser would
not become owner, but it would already have an account it was never invited
to. `reserved_at` lets an abandoned reservation be taken over after a bounded
wait; `claimed_at` marks it bound.

**No backfill here.** Only the running backend knows whether this is a Desktop
installation (`DEPLOYMENT_KIND`), and on a hosted deployment the oldest user is
not an owner of anything. The identity service claims the oldest active account
lazily, on a Desktop installation only, the first time it is asked -- which is
also the only place that can do it race-free against a concurrent signup.

`user_id` is `ON DELETE SET NULL`, not `CASCADE`: a deleted owner leaves the
slot taken rather than handing it to the next person to sign up.

Revision ID: 0040_installation_owner
Revises: 0039_whatsapp_number_pool
"""

import sqlalchemy as sa
from alembic import op

revision = "0040_installation_owner"
down_revision = "0039_whatsapp_number_pool"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "installation_owner",
        sa.Column(
            "singleton",
            sa.Boolean(),
            primary_key=True,
            server_default=sa.text("true"),
            nullable=False,
        ),
        sa.Column("email", sa.String(255), nullable=False),
        sa.Column(
            "user_id",
            sa.Uuid(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
            unique=True,
        ),
        sa.Column("reserved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("claimed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint("singleton", name="ck_installation_owner_singleton"),
    )


def downgrade() -> None:
    op.drop_table("installation_owner")
