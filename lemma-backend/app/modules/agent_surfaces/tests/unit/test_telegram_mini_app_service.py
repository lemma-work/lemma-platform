from __future__ import annotations

import pytest

from app.core.config import settings
from app.modules.agent_surfaces.services.telegram_mini_app_service import (
    telegram_mini_app_url,
)

pytestmark = pytest.mark.unit


def test_telegram_mini_app_uses_canonical_cloud_app_origin(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    monkeypatch.setattr(settings, "app_base_domain", "apps.example.test")

    assert (
        telegram_mini_app_url(public_slug="support-desk")
        == "https://support-desk.apps.example.test"
    )


def test_telegram_mini_app_does_not_create_local_app_serving_path(monkeypatch):
    monkeypatch.setattr(settings, "environment", "local")
    monkeypatch.setattr(settings, "api_url", "https://temporary.trycloudflare.com")
    monkeypatch.setattr(
        settings,
        "app_base_domain",
        "apps.lemma.localhost:8710",
    )

    assert telegram_mini_app_url(public_slug="support-desk") is None
