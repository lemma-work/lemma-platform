"""Web forms built from a table: which table, which columns, what to say.

**`agent_surface_web_widgets.form_spec`** holds a form a member built by picking
a table and ticking the columns to ask for, instead of writing a function for
it: the table, the fields (column, label, what kind of answer, required), an
intro and the thank-you. A submission adds one row to that table, as the member
who looks after the form. Null for a chat, and for a form that still runs a
function, so nothing existing changes.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0045_web_forms"
down_revision = "0044_contacts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "agent_surface_web_widgets",
        sa.Column("form_spec", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("agent_surface_web_widgets", "form_spec")
