"""Find a pod's folders by visibility.

Hiding a file under a folder its reader cannot open starts from the folders in
the pod that might be unreadable -- restricted and personal ones, mostly -- and
it is asked on every search and every tree. Nothing indexed folders on their
own, so the question read every row of the pod. Folders only, so file writes
pay nothing for it.
"""

import sqlalchemy as sa
from alembic import op

revision = "0053_folder_visibility_index"
down_revision = "0052_workflow_run_times_utc"
branch_labels = None
depends_on = None


_INDEX = "ix_datastore_file_pod_folder_visibility"


def _repair_invalid(name: str) -> None:
    """Drop a half-built index so `IF NOT EXISTS` stops mistaking it for done.

    An interrupted `CREATE INDEX CONCURRENTLY` leaves the index behind with
    `indisvalid = false`. The name exists, so on the rerun `IF NOT EXISTS`
    skips the build, the revision is stamped, and the planner ignores the
    index for good -- the blocked-folders read it serves scans the pod on every
    search and tree. Checking first is what makes a rerun recover.
    """
    half_built = (
        op.get_bind()
        .execute(
            sa.text(
                "SELECT 1 FROM pg_index i JOIN pg_class c ON c.oid = i.indexrelid "
                "WHERE c.relname = :name AND NOT i.indisvalid"
            ),
            {"name": name},
        )
        .scalar()
    )
    if half_built:
        op.execute(f"DROP INDEX CONCURRENTLY IF EXISTS {name}")


def upgrade() -> None:
    # Concurrently: the file table is written on every upload, and a plain
    # build would block those writers for the length of the scan.
    with op.get_context().autocommit_block():
        _repair_invalid(_INDEX)
        op.create_index(
            _INDEX,
            "datastore_files",
            ["pod_id", "visibility"],
            postgresql_where=sa.text("kind = 'FOLDER'"),
            postgresql_concurrently=True,
            if_not_exists=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            _INDEX,
            table_name="datastore_files",
            postgresql_concurrently=True,
            if_exists=True,
        )
