"""Connected machines pair with a code a person approves.

A person's own laptop or VM joins with `lemma machine connect`
(docs/architecture/connected-machines.md). The machine asks for a code; the
person approves it signed in; the machine then claims the connection with a
key only it holds, and the secret is minted in that claim. So
`machine_connections` holds rows that exist before they have a secret --
`secret_hash` is null between the approval and the claim -- and before the
machine has said where it is -- `home` is null until its first ``hello``. It
records the `installation_id` that claimed it and the `platform` it runs on.

`machine_connection_pairings` holds a code a machine asked for until it is
claimed or expires: the hash of the code and of the claim key; approving
fills `connection_id`.

`0074_machines` shipped before this, so this is its own revision rather than
a section of that one. `CONNECTION_COLUMNS` and `TABLES` are the DDL exactly as
the models compile it, pinned to them by
``workspace/tests/unit/test_machine_ddl_matches_models``.

Downgrade revokes the connections never claimed -- no machine holds a secret
for them -- giving each a placeholder hash no secret can match, and gives every
connection with no reported `home` an empty one, before restoring the NOT
NULLs. Revoking rather than deleting keeps the machines that name them
pointing at a row.

Revision ID: 0075_machine_connection_pairing
Revises: 0074_machines
"""

from alembic import op

revision = "0075_machine_connection_pairing"
down_revision = "0074_machines"
branch_labels = None
depends_on = None

CONNECTION_COLUMNS = (
    "installation_id TEXT",
    "platform TEXT",
)

NOW_NULLABLE = ("secret_hash", "home")

TABLES: dict[str, tuple[str, ...]] = {
    "machine_connection_pairings": (
        "CREATE TABLE machine_connection_pairings ( code_hash VARCHAR(64) NOT NULL, claim_key_hash VARCHAR(64) NOT NULL, display_name VARCHAR(255) NOT NULL, mode VARCHAR(16) NOT NULL, platform VARCHAR(32) NOT NULL, expires_at TIMESTAMP WITH TIME ZONE NOT NULL, connection_id UUID, id UUID NOT NULL, created_at TIMESTAMP WITH TIME ZONE NOT NULL, PRIMARY KEY (id), CONSTRAINT uq_machine_connection_pairings_code UNIQUE (code_hash), FOREIGN KEY(connection_id) REFERENCES machine_connections (id) ON DELETE CASCADE )",
        "CREATE INDEX ix_machine_connection_pairings_expires_at ON machine_connection_pairings (expires_at)",
    ),
}


def upgrade() -> None:
    for column in NOW_NULLABLE:
        op.execute(
            f"ALTER TABLE machine_connections ALTER COLUMN {column} DROP NOT NULL"
        )
    for column in CONNECTION_COLUMNS:
        op.execute(f"ALTER TABLE machine_connections ADD COLUMN IF NOT EXISTS {column}")
    for statements in TABLES.values():
        for statement in statements:
            op.execute(statement)


def downgrade() -> None:
    for table in reversed(TABLES):
        op.execute(f"DROP TABLE {table}")
    # A connection approved but never claimed is revoked below; its machine row
    # is retired with it, as revoking does, so the name is free again after a
    # re-upgrade -- with the pairing table gone nothing else would expire it.
    op.execute(
        "UPDATE sandboxes SET desired_state = 'deleted', "
        "slug = '~' || replace(id::text, '-', ''), updated_at = now() "
        "WHERE id IN (SELECT id FROM machine_connections WHERE secret_hash IS NULL)"
    )
    op.execute(
        "UPDATE machine_connections SET secret_hash = 'unclaimed:' || id::text, "
        "revoked_at = coalesce(revoked_at, now()) WHERE secret_hash IS NULL"
    )
    op.execute("UPDATE machine_connections SET home = '' WHERE home IS NULL")
    for column in reversed(CONNECTION_COLUMNS):
        op.execute(
            f"ALTER TABLE machine_connections DROP COLUMN IF EXISTS {column.split()[0]}"
        )
    for column in NOW_NULLABLE:
        op.execute(
            f"ALTER TABLE machine_connections ALTER COLUMN {column} SET NOT NULL"
        )
