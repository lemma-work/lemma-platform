"""Let two organizations be called the same thing.

``organizations.name`` carried a unique constraint that made display names
unique across the whole deployment. The slug is the handle; the name is a
label, and labels are not scarce. The global uniqueness had two consequences
the product never promised: on a shared deployment, the first customer to
register a common name took it from everyone else with no recovery and no
acceptable explanation — and both the 409 and ``is_name_available`` were an
existence oracle for which organizations exist on the deployment, including
names that confirm a competitor is present.

The unique index goes; the ordinary index stays, because name lookups remain
(they just no longer answer "is this free"). Slug uniqueness is untouched.
See PS-ONB-014 and DEV-ONB-002.
"""

from alembic import op


revision = "0021_org_names_not_unique"
down_revision = "0020_schedule_run_last_inspected"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.drop_index("ix_organizations_name", table_name="organizations")
    op.create_index("ix_organizations_name", "organizations", ["name"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_organizations_name", table_name="organizations")
    op.create_index(
        op.f("ix_organizations_name"), "organizations", ["name"], unique=True
    )
