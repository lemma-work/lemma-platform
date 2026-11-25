"""How many answers a day one form takes: ``datastore_public_rows.answers_per_day``.

A form is a table opened to people outside, and the day's ceiling on its
answers was one number for the whole deployment, counted per widget, so every
form behind one key shared it and a busy one refused people on its launch day.
The member who opens a table may now set its own, up to the deployment's
ceiling. A column rather than JSON: the grant has no JSON for its own facts
(``columns`` is the list of open columns), and the visitor's write path reads
it on every answer.

NULL for every existing grant -- the deployment's default, as before -- so
adding it rewrites no row. Reruns cleanly.

Revision ID: 0077_public_rows_answers_per_day
Revises: 0076_reports_and_blocks
"""

from alembic import op

revision = "0077_public_rows_answers_per_day"
down_revision = "0076_reports_and_blocks"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE datastore_public_rows "
        "ADD COLUMN IF NOT EXISTS answers_per_day INTEGER"
    )


def downgrade() -> None:
    op.drop_column("datastore_public_rows", "answers_per_day")
