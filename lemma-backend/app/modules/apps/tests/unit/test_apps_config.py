import pytest
from pydantic import ValidationError

from app.modules.apps.config import AppsSettings


def test_apps_settings_own_archive_limits(monkeypatch):
    expected = {
        "app_source_archive_max_bytes": 100 * 1024 * 1024,
        "app_dist_archive_max_bytes": 100 * 1024 * 1024,
        "app_bundle_upload_max_bytes": 200 * 1024 * 1024,
        "app_archive_max_entries": 10_000,
        "app_archive_max_uncompressed_bytes": 400 * 1024 * 1024,
        "app_archive_max_compression_ratio": 200,
        # Release retention. The floor and the ceiling are both load-bearing:
        # keep_last keeps a dormant app rollback-able, max_keep is what bounds a
        # burst of deploys. See app.core.retention.
        "app_release_retention_enabled": True,
        "app_release_keep_last": 10,
        "app_release_keep_days": 30,
        "app_release_max_keep": 20,
        "app_release_retention_cron": "20 4 * * *",
        "app_release_retention_batch": 200,
        "app_release_retention_budget_seconds": 60.0,
        # Moved from `app/core/config.py`: only this module reads it.
        "app_branding_enabled": True,
    }
    assert set(AppsSettings.model_fields) == set(expected)
    for field, default in expected.items():
        assert AppsSettings.model_fields[field].default == default

    monkeypatch.setenv("APP_ARCHIVE_MAX_ENTRIES", "17")
    assert AppsSettings().app_archive_max_entries == 17


def test_retention_ceiling_cannot_be_lower_than_its_floor(monkeypatch):
    monkeypatch.setenv("APP_RELEASE_KEEP_LAST", "10")
    monkeypatch.setenv("APP_RELEASE_MAX_KEEP", "2")
    with pytest.raises(ValidationError, match="APP_RELEASE_MAX_KEEP"):
        AppsSettings()
