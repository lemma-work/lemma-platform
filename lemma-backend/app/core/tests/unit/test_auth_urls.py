"""Unit tests for the auth UI URL handed to clients.

The bug these exist to prevent: advertising the bare origin. The browser SDK
derives its routes from the pathname it is given and the CLI appends
``/cli/login``, so an origin without the base path sends both to routes that do
not exist.
"""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.core import auth_urls
from app.core.auth_urls import apply_auth_base_path, auth_ui_url, cli_auth_ui_url
from app.core.runtime_config import build_runtime_config


@pytest.fixture
def auth_settings(monkeypatch):
    def _set(*, frontend: str, base_path: str = "/auth", cli: str | None = None):
        monkeypatch.setattr(auth_urls.settings, "auth_frontend_url", frontend)
        monkeypatch.setattr(auth_urls.settings, "auth_website_base_path", base_path)
        monkeypatch.setattr(auth_urls.settings, "cli_auth_frontend_url", cli)

    return _set


def test_base_path_is_joined_onto_the_origin(auth_settings):
    auth_settings(frontend="https://lemma.work")
    assert auth_ui_url() == "https://lemma.work/auth"


def test_joining_is_idempotent(auth_settings):
    # A deployment already pointing at the mounted path is configured correctly
    # and must not end up at /auth/auth.
    auth_settings(frontend="https://lemma.work/auth")
    assert auth_ui_url() == "https://lemma.work/auth"


def test_trailing_slash_does_not_double_up(auth_settings):
    auth_settings(frontend="https://lemma.work/")
    assert auth_ui_url() == "https://lemma.work/auth"


def test_a_root_mounted_ui_is_left_alone(auth_settings):
    auth_settings(frontend="https://auth.lemma.work", base_path="/")
    assert auth_ui_url() == "https://auth.lemma.work"


def test_a_host_ending_in_auth_is_not_mistaken_for_the_path(auth_settings):
    # "myauth" ends in "auth" but is not the base path.
    auth_settings(frontend="https://example.com/myauth")
    assert auth_ui_url() == "https://example.com/myauth/auth"


def test_a_nested_base_path_is_joined_whole(auth_settings):
    auth_settings(frontend="https://lemma.work", base_path="/accounts/auth")
    assert auth_ui_url() == "https://lemma.work/accounts/auth"


def test_the_cli_url_prefers_its_own_host_and_still_gets_the_path(auth_settings):
    auth_settings(frontend="https://lemma.work", cli="http://localhost:3710")
    assert cli_auth_ui_url() == "http://localhost:3710/auth"


def test_the_cli_falls_back_to_the_browser_url(auth_settings):
    auth_settings(frontend="https://lemma.work", cli=None)
    assert cli_auth_ui_url() == "https://lemma.work/auth"


def test_a_served_app_is_handed_the_auth_ui_not_the_origin(auth_settings):
    auth_settings(frontend="https://lemma.work")
    config = build_runtime_config(uuid4())
    # The SDK reads this pathname to build sign-in and callback routes.
    assert config["authUrl"] == "https://lemma.work/auth"


def test_apply_is_usable_on_an_arbitrary_base(auth_settings):
    auth_settings(frontend="https://lemma.work")
    assert apply_auth_base_path("https://other.example") == "https://other.example/auth"
