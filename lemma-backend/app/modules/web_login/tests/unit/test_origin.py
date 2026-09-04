from __future__ import annotations

import pytest

from app.modules.web_login.services.origin import InvalidOrigin, normalize_origin


@pytest.mark.parametrize(
    "given,expected",
    [
        ("https://app.example.com", "https://app.example.com"),
        # The page a login starts at is incidental; the session is not.
        ("https://app.example.com/login?next=/reports", "https://app.example.com"),
        ("https://APP.Example.COM/", "https://app.example.com"),
        ("app.example.com", "https://app.example.com"),
        ("  https://app.example.com  ", "https://app.example.com"),
        ("https://app.example.com:443/x", "https://app.example.com"),
        ("http://localhost:3000/app", "http://localhost:3000"),
        ("https://app.example.com:8443", "https://app.example.com:8443"),
    ],
)
def test_an_origin_is_scheme_and_host(given: str, expected: str) -> None:
    assert normalize_origin(given) == expected


def test_a_bare_host_is_read_as_https() -> None:
    """The alternative is silently saving a session against a plaintext origin
    nobody asked for."""
    assert normalize_origin("example.com").startswith("https://")


@pytest.mark.parametrize("given", ["", "   ", "ftp://files.example.com", "https://"])
def test_what_a_session_cannot_belong_to_is_refused(given: str) -> None:
    with pytest.raises(InvalidOrigin):
        normalize_origin(given)


def test_two_pages_of_one_site_are_one_login() -> None:
    assert normalize_origin("https://x.test/login") == normalize_origin(
        "https://x.test/account/settings"
    )
