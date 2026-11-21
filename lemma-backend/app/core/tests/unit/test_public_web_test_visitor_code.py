"""PUBLIC_WEB_TEST_VISITOR_CODE: a known visitor code for test servers, never production."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from app.core.config import settings
from app.core.public_web import PublicWebSettings


def _settings(**values: object) -> PublicWebSettings:
    # No dotenv: the developer's own .env must not decide what this test sees.
    return PublicWebSettings(_env_file=None, **values)


def test_codes_are_random_unless_a_test_server_names_one():
    assert _settings().public_web_test_visitor_code is None


def test_a_test_server_may_name_the_code(monkeypatch):
    # A hosted test server is `development`; local and testing are laxer still.
    monkeypatch.setattr(settings, "environment", "development")
    named = _settings(public_web_test_visitor_code="424242")
    assert named.public_web_test_visitor_code == "424242"


def test_production_refuses_to_start_with_a_known_code(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    with pytest.raises(ValidationError, match="must not be set in production"):
        _settings(public_web_test_visitor_code="424242")


def test_production_starts_when_codes_are_random(monkeypatch):
    monkeypatch.setattr(settings, "environment", "production")
    assert _settings().public_web_test_visitor_code is None


@pytest.mark.parametrize("code", ["12345", "1234567", "abcdef", "12 456"])
def test_the_code_is_six_digits_like_every_other(monkeypatch, code):
    monkeypatch.setattr(settings, "environment", "development")
    with pytest.raises(ValidationError):
        _settings(public_web_test_visitor_code=code)
