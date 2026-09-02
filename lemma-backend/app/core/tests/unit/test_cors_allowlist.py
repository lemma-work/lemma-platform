"""Which origins a deployment lets read authenticated responses.

`CORS_ORIGINS` defaults to eight loopback origins plus the Tauri schemes, and
the middleware is installed with `allow_credentials=True`. A production
deployment that sets `FRONTEND_URL` and `API_URL` -- which is what the
configuration guide's URL block shows -- and never thinks about `CORS_ORIGINS`
therefore shipped with `http://localhost:3000` and friends as *credentialed*
allowed origins. Any page the victim can be made to load on one of those ports
-- a dev server, another product's local UI, something they were talked into
running -- could then call the production API with their session and read the
answer.

The defaults exist for a real reason (a checkout, `make dev`, the desktop
build), so they are kept where they are for and dropped where they are a hole.
An operator who names loopback origins explicitly still gets them: that is a
choice, not an unnoticed default.
"""

from __future__ import annotations

import re

import pytest

from app.core import cors


@pytest.fixture
def _origins(monkeypatch):
    """A deployment that configured its URLs and nothing else."""
    monkeypatch.setattr(cors.settings, "frontend_url", "https://app.lemma.work")
    monkeypatch.setattr(cors.settings, "auth_frontend_url", "https://app.lemma.work/auth")
    monkeypatch.setattr(
        cors.settings,
        "cors_origins",
        ["http://localhost:3000", "http://127.0.0.1:5173", "tauri://localhost"],
    )
    # Attribute assignment marks the field explicitly set, which is exactly the
    # distinction under test -- so the default case has to be restored by hand.
    cors.settings.model_fields_set.discard("cors_origins")


def _not_local(monkeypatch) -> None:
    monkeypatch.setattr(cors.settings, "environment", "production")


def test_loopback_defaults_are_not_allowed_in_production(_origins, monkeypatch):
    _not_local(monkeypatch)

    origins = cors.get_allowed_cors_origins()

    assert origins == ["https://app.lemma.work"]


def test_loopback_defaults_are_allowed_locally(_origins, monkeypatch):
    monkeypatch.setattr(cors.settings, "environment", "local")

    origins = cors.get_allowed_cors_origins()

    assert "http://localhost:3000" in origins
    assert "tauri://localhost" in origins


def test_explicitly_configured_loopback_origins_are_honoured(_origins, monkeypatch):
    """An operator who names them has decided; only the default is dropped."""
    _not_local(monkeypatch)
    cors.settings.model_fields_set.add("cors_origins")

    origins = cors.get_allowed_cors_origins()

    assert "http://localhost:3000" in origins


def test_the_apps_domain_is_https_only_in_production(monkeypatch):
    """A plain-HTTP page on the apps domain must not be a credentialed origin."""
    _not_local(monkeypatch)
    monkeypatch.setattr(cors.settings, "app_base_domain", "apps.lemma.work")
    monkeypatch.setattr(cors.settings, "cors_origin_regex", None)

    pattern = cors.get_allowed_cors_origin_regex()

    assert re.fullmatch(pattern, "https://home-abc.apps.lemma.work")
    assert not re.fullmatch(pattern, "http://home-abc.apps.lemma.work")


def test_a_loopback_apps_domain_still_allows_http(monkeypatch):
    """`.localhost` is reserved loopback; there is no TLS there to insist on."""
    _not_local(monkeypatch)
    monkeypatch.setattr(cors.settings, "app_base_domain", "apps.lemma.localhost:8711")
    monkeypatch.setattr(cors.settings, "cors_origin_regex", None)

    pattern = cors.get_allowed_cors_origin_regex()

    assert re.fullmatch(pattern, "http://home-x.apps.lemma.localhost:8711")
