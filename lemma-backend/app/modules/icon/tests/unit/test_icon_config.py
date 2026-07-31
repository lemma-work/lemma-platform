from app.modules.icon.config import IconSettings


def test_icon_settings_own_image_limits(monkeypatch):
    assert set(IconSettings.model_fields) == {
        "icon_upload_max_bytes",
        "icon_max_dimension_pixels",
        "icon_max_total_pixels",
    }
    assert IconSettings.model_fields["icon_upload_max_bytes"].default == 5 * 1024 * 1024
    assert IconSettings.model_fields["icon_max_dimension_pixels"].default == 4096
    assert IconSettings.model_fields["icon_max_total_pixels"].default == 16_777_216

    monkeypatch.setenv("ICON_MAX_DIMENSION_PIXELS", "2048")
    assert IconSettings().icon_max_dimension_pixels == 2048
