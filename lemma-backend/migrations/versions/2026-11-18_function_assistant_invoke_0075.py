"""A function a Lemma's assistant is not offered: ``functions.assistant_invoke``.

A plugin keeps some functions for its own flows -- an app's "claim" that the
app calls after checking a daily limit, say. The assistant saw every function
and ran it by name, so it could step around the limit the app enforced. A
function with ``assistant_invoke`` false is not listed to the assistant and is
refused when the assistant runs it; apps, workflows, schedules and other
functions still run it.

A column rather than a key in the function's ``config``: that JSON is the
function's own, handed to its code as ``ctx.config`` and checked against its
config schema, and the function row has no other JSON for platform facts.
Default true, so every function stays as it was. Adding a column with a
constant default rewrites no rows. Reruns cleanly.

Revision ID: 0075_function_assistant_invoke
Revises: 0074_machines
"""

from alembic import op

revision = "0075_function_assistant_invoke"
down_revision = "0074_machines"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE functions "
        "ADD COLUMN IF NOT EXISTS assistant_invoke BOOLEAN NOT NULL DEFAULT true"
    )


def downgrade() -> None:
    op.drop_column("functions", "assistant_invoke")
