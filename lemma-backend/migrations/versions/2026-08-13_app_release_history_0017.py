"""Give an app's releases an identity, their own source, and a prune marker.

Every bundle upload has always written an ``app_releases`` row and kept its dist
bytes forever, with ``apps.current_release_id`` naming the live one. So the
history was already on disk -- there was just no way to name a release, no way
to see it, and no way to move the pointer. This migration supplies the missing
columns.

``release_number`` is a per-app counter. The natural identity of a release is
its dist digest, but a sha256 is 64 hex characters: too long for a DNS label
(63 max, and previews are served at ``<slug>--r<N>.<app_base_domain>``) and
unreadable in a list. ``v7`` is both, and the digest stays as the identity.

``source_archive_path`` moves source to where it belongs. It has lived on the
``apps`` row -- one column, overwritten by every upload -- so rolling the dist
back would have paired an old build with the newest source, and an export after
that rollback would have shipped code that never produced the running build.
The backfill can only attribute the app's current source to its newest release;
older releases keep a NULL source, which reads honestly as "we do not know".

``pruned_at`` marks a release whose bytes retention has deleted. The row stays:
history that skips v3 with no explanation is worse than history that says the
build was removed, and ``dist_root_path`` keeps its value as a record of where
the bytes were.

Revision ID: 0017_app_release_history
Revises: 0016_unique_surface_email
"""

from alembic import op
import sqlalchemy as sa


revision = "0017_app_release_history"
down_revision = "0016_unique_surface_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("app_releases", sa.Column("release_number", sa.Integer(), nullable=True))
    op.add_column(
        "app_releases", sa.Column("source_archive_path", sa.String(), nullable=True)
    )
    op.add_column("app_releases", sa.Column("source_digest", sa.String(), nullable=True))
    op.add_column("app_releases", sa.Column("created_by", sa.UUID(), nullable=True))
    op.add_column("app_releases", sa.Column("label", sa.String(), nullable=True))
    op.add_column(
        "app_releases",
        sa.Column("pruned_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_app_release_created_by",
        "app_releases",
        "users",
        ["created_by"],
        ["id"],
        ondelete="SET NULL",
    )

    # Number every existing release oldest-first, so v1 is genuinely the first
    # build. `id` breaks ties: these are uuid7s, so it is creation order anyway.
    op.execute(
        """
        UPDATE app_releases AS r
        SET release_number = numbered.row_number
        FROM (
            SELECT id,
                   ROW_NUMBER() OVER (
                       PARTITION BY app_id ORDER BY created_at ASC, id ASC
                   ) AS row_number
            FROM app_releases
        ) AS numbered
        WHERE r.id = numbered.id
        """
    )

    # The app's single source archive can only be claimed by its newest release.
    # Attributing it to any older one would be a guess, and the source path is
    # content-addressed (`source/<sha256>/archive.zip`), so the digest comes
    # straight out of the path rather than needing the bytes.
    op.execute(
        """
        UPDATE app_releases AS r
        SET source_archive_path = a.source_archive_path,
            source_digest = split_part(a.source_archive_path, '/', 2)
        FROM apps AS a
        WHERE a.source_archive_path IS NOT NULL
          AND r.app_id = a.id
          AND r.id = (
              SELECT newest.id
              FROM app_releases AS newest
              WHERE newest.app_id = a.id
              ORDER BY newest.created_at DESC, newest.id DESC
              LIMIT 1
          )
        """
    )

    op.alter_column("app_releases", "release_number", nullable=False)
    op.create_unique_constraint(
        "uq_app_release_number", "app_releases", ["app_id", "release_number"]
    )
    op.create_index(
        "ix_app_release_app_created",
        "app_releases",
        ["app_id", sa.text("created_at DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_app_release_app_created", table_name="app_releases")
    op.drop_constraint("uq_app_release_number", "app_releases", type_="unique")
    op.drop_constraint("fk_app_release_created_by", "app_releases", type_="foreignkey")
    op.drop_column("app_releases", "pruned_at")
    op.drop_column("app_releases", "label")
    op.drop_column("app_releases", "created_by")
    op.drop_column("app_releases", "source_digest")
    op.drop_column("app_releases", "source_archive_path")
    op.drop_column("app_releases", "release_number")
