from app.modules.apps.config import AppsSettings


def test_apps_settings_own_archive_limits(monkeypatch):
    expected = {
        "app_source_archive_max_bytes": 100 * 1024 * 1024,
        "app_dist_archive_max_bytes": 100 * 1024 * 1024,
        "app_bundle_upload_max_bytes": 200 * 1024 * 1024,
        "app_archive_max_entries": 10_000,
        "app_archive_max_uncompressed_bytes": 400 * 1024 * 1024,
        "app_archive_max_compression_ratio": 200,
    }
    assert set(AppsSettings.model_fields) == set(expected)
    for field, default in expected.items():
        assert AppsSettings.model_fields[field].default == default

    monkeypatch.setenv("APP_ARCHIVE_MAX_ENTRIES", "17")
    assert AppsSettings().app_archive_max_entries == 17
