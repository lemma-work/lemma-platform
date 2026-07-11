from pathlib import Path


def test_file_integrity_migration_adds_only_content_sha256():
    migration = (
        Path(__file__).resolve().parents[5]
        / "migrations/versions/2026-07-11_datastore_file_integrity_0004.py"
    ).read_text()

    assert migration.count("op.add_column(") == 1
    assert (
        'sa.Column("content_sha256", sa.String(length=64), nullable=True)' in migration
    )
    for removed in (
        '"storage_key"',
        '"content_revision"',
        '"processing_phase"',
        '"processing_started_at"',
    ):
        assert removed not in migration
