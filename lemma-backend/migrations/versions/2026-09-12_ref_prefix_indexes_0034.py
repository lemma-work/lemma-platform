"""Let a release or revision be found by a digest prefix without reading them all.

Both resolvers take a ref that may be a prefix of a content digest, and both
answered it by loading every release (or revision) the owner has ever had and
running `startswith` in Python. Neither table is ever emptied -- retention
stamps `pruned_at`/`purged_at` and leaves the rows -- so resolving one ref cost
an app's entire deploy history.

Moving the match into SQL only helps if an index can serve it. A plain btree on
text cannot answer `LIKE 'abc%'` under a non-C collation, because the rows
sharing a prefix are not contiguous in collation order; `text_pattern_ops`
orders by byte instead, which is what makes a prefix comparison index-driven.
`ix_datastore_file_pod_path_prefix` carries it in the baseline schema for the
same reason.

The existing `uq_function_revision_active_hash` looks like it would do, and does
not: it is partial on `pruned_at IS NULL`, while ref resolution has to see
pruned rows so it can tell "removed by retention" from "never existed".

The accounting, in the terms 0018 set: two added, one dropped.
`ix_app_release_app_id` pays for one of them. It indexes `app_id` alone, which
already leads `ix_app_release_app_created`, `uq_app_release_number` and now the
new prefix index, so every lookup it served is served by a composite that was
already being maintained. `function_revisions` has no such redundancy and pays
the new index outright.

Created non-concurrently, as in 0018: CONCURRENTLY cannot run inside Alembic's
transaction, and both tables are small -- one row per deployment.

Revision ID: 0034_ref_prefix_indexes
Revises: 0033_datastore_signed_links
"""

from alembic import op

revision = "0034_ref_prefix_indexes"
down_revision = "0033_datastore_signed_links"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_app_release_app_version_prefix",
        "app_releases",
        ["app_id", "version"],
        postgresql_ops={"version": "text_pattern_ops"},
    )
    op.create_index(
        "ix_function_revision_function_hash_prefix",
        "function_revisions",
        ["function_id", "revision_hash"],
        postgresql_ops={"revision_hash": "text_pattern_ops"},
    )
    op.drop_index("ix_app_release_app_id", table_name="app_releases")


def downgrade() -> None:
    op.create_index("ix_app_release_app_id", "app_releases", ["app_id"], unique=False)
    op.drop_index(
        "ix_function_revision_function_hash_prefix", table_name="function_revisions"
    )
    op.drop_index("ix_app_release_app_version_prefix", table_name="app_releases")
