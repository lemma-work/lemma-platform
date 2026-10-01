"""Private apps lead to the configured portal rather than the site homepage."""

import pytest

from app.core.config import settings
from app.modules.identity.config import identity_settings
from app.modules.identity.contracts.app_sessions import app_sign_in_url

pytestmark = pytest.mark.unit


@pytest.mark.parametrize(
    ("auth_url", "base_path", "expected"),
    [
        (
            "https://workspace.example.test",
            "/auth",
            "https://workspace.example.test/auth",
        ),
        (
            "https://workspace.example.test/",
            "/login",
            "https://workspace.example.test/login",
        ),
        (
            "https://workspace.example.test/portal",
            "/auth",
            "https://workspace.example.test/portal",
        ),
    ],
)
def test_app_sign_in_url_uses_the_configured_portal(
    monkeypatch, auth_url, base_path, expected
):
    monkeypatch.setattr(settings, "auth_frontend_url", str(auth_url))
    monkeypatch.setattr(identity_settings, "auth_website_base_path", str(base_path))
    assert app_sign_in_url() == expected
