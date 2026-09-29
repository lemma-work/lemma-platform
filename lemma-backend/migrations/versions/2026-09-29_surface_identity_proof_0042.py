"""Record how each verified surface identity was proven.

A verified identity on the shared Telegram or WhatsApp bot was only ever
recognised through a verified phone that still matched the account's mobile
number. That was the right rule for the proofs that existed -- a contact share,
or a WhatsApp sender's own number -- and the wrong one for an identity proven
some other way: an email code with no phone behind it, or a one-time link the
signed-in user opened from the Lemma app. Those were written and then never
recognised.

``proof`` names the kind, so the phone rule applies to phone proofs only and a
mobile-number change still revokes exactly those.

Existing rows are backfilled from what they hold: a row with a verified phone
is a phone proof, since that is the rule it has been read under until now, and
one without is an email proof, since an email code is the only other way a row
was ever written. Both are the reading the old code already gave them, except
that a phone-less row on a shared bot now resolves -- which is the inconsistency
this fixes, not a new permission: the same row already resolved on the
onboarding side.

Revision ID: 0042_surface_identity_proof
Revises: 0041_conversation_last_activity
"""

import sqlalchemy as sa
from alembic import op

revision = "0042_surface_identity_proof"
down_revision = "0041_conversation_last_activity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "surface_verified_identities",
        sa.Column("proof", sa.String(16), nullable=True),
    )
    op.execute(
        sa.text(
            "UPDATE surface_verified_identities SET proof = CASE "
            "WHEN verified_phone IS NOT NULL THEN 'phone' ELSE 'email' END "
            "WHERE proof IS NULL"
        )
    )
    op.alter_column("surface_verified_identities", "proof", nullable=False)
    op.create_check_constraint(
        "ck_surface_identity_proof_kind",
        "surface_verified_identities",
        "proof IN ('phone', 'email', 'link_token')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_surface_identity_proof_kind",
        "surface_verified_identities",
        type_="check",
    )
    op.drop_column("surface_verified_identities", "proof")
