"""A contact who proved to be one of the pod's members.

A member whose Telegram account Lemma did not know was, in a group, a contact.
Once they prove the account, the contact the pod made for it is them: it holds
nothing, is no longer listed or counted, and says whom it turned out to be --
``merged_into_user_id``, beside ``merged_into_id`` for a contact that turned out
to be another contact. A contact is never both at once.

Expand-only: a nullable column, its foreign key (SET NULL, so a member deleted
leaves a contact that was them rather than taking the row) and the check.
Nothing is backfilled.

And an index on a handle across pods (``ix_contact_identities_kind_value``),
built concurrently: a member proving an account looks for the contacts that
hold it in every pod they are in, by kind and value, which the per-pod unique
key cannot serve.

Revision ID: 0067_contact_became_member
Revises: 0066_session_proved_identity
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0067_contact_became_member"
down_revision = "0066_session_proved_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "contacts",
        sa.Column(
            "merged_into_user_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey(
                "users.id",
                ondelete="SET NULL",
                name="fk_contacts_merged_into_user",
            ),
            nullable=True,
        ),
    )
    op.create_check_constraint(
        "ck_contacts_merged_once",
        "contacts",
        "merged_into_id IS NULL OR merged_into_user_id IS NULL",
    )
    with op.get_context().autocommit_block():
        op.create_index(
            "ix_contact_identities_kind_value",
            "contact_identities",
            ["kind", "value"],
            unique=False,
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            "ix_contact_identities_kind_value",
            table_name="contact_identities",
            postgresql_concurrently=True,
            if_exists=True,
        )
    op.drop_constraint("ck_contacts_merged_once", "contacts", type_="check")
    op.drop_column("contacts", "merged_into_user_id")
