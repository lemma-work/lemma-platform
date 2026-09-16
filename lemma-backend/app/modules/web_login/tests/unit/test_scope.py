"""Narrowing a captured browser state to the site it belongs to.

The first version of this feature captured the whole shared browser profile --
every site the agent had ever visited -- and stored it under one site's name, to
be restored whenever any agent asked for that site. These are the tests that
make that impossible.
"""

from __future__ import annotations

from app.modules.web_login.services.scope import (
    domain_matches,
    host_of,
    looks_signed_in,
    scope_state,
)


def _cookie(name: str, domain: str) -> dict:
    return {"name": name, "value": "v", "domain": domain, "path": "/"}


def test_another_site_visited_in_the_same_browser_is_not_captured() -> None:
    """The exact leak the first version shipped."""
    state = {
        "cookies": [
            _cookie("session", "app.example.com"),
            _cookie("github_session", "github.com"),
            _cookie("bank", ".bank.test"),
        ],
        "origins": [],
    }
    scoped = scope_state(state, origin="https://app.example.com")
    assert [c["name"] for c in scoped["cookies"]] == ["session"]


def test_a_cookie_the_site_would_receive_is_kept() -> None:
    """A parent-domain cookie is sent to the subdomain, so it is part of the login."""
    state = {"cookies": [_cookie("sso", ".example.com")], "origins": []}
    scoped = scope_state(state, origin="https://accounts.example.com")
    assert len(scoped["cookies"]) == 1


def test_a_sibling_subdomain_is_not_kept() -> None:
    """`other.example.com` cookies are never sent to `accounts.example.com`."""
    state = {"cookies": [_cookie("other", "other.example.com")], "origins": []}
    assert scope_state(state, origin="https://accounts.example.com")["cookies"] == []


def test_a_lookalike_domain_is_not_a_match() -> None:
    assert domain_matches("example.com", "example.com.attacker.test") is False
    assert domain_matches("example.com", "notexample.com") is False
    assert domain_matches("example.com", "sub.example.com") is True


def test_a_public_suffix_cookie_does_not_match_everything() -> None:
    """Browsers refuse to set these, so one can only arrive from a hand-built
    state -- and a filter that trusted that would return every cookie in it."""
    assert domain_matches("com", "app.example.com") is False
    assert domain_matches(".com", "app.example.com") is False


def test_local_storage_is_kept_only_for_the_exact_origin() -> None:
    """There is no subdomain rule for storage in the browser, so none here."""
    state = {
        "cookies": [],
        "origins": [
            {"origin": "https://app.example.com", "localStorage": [{"name": "a"}]},
            {"origin": "https://evil.test", "localStorage": [{"name": "b"}]},
            {"origin": "https://other.example.com", "localStorage": [{"name": "c"}]},
        ],
    }
    scoped = scope_state(state, origin="https://app.example.com")
    assert [o["origin"] for o in scoped["origins"]] == ["https://app.example.com"]


def test_an_empty_capture_does_not_look_signed_in() -> None:
    """Somebody pressed the button on a page they had not signed in to.

    Storing this would mean telling them it was kept and asking again next run.
    """
    assert (
        looks_signed_in({"cookies": [], "origins": []}, origin="https://a.test")
        is False
    )
    assert (
        looks_signed_in(
            {"cookies": [_cookie("s", "other.test")], "origins": []},
            origin="https://a.test",
        )
        is False
    ), "cookies for a different site are not this site's login"


def test_a_capture_with_the_site_s_cookie_looks_signed_in() -> None:
    state = {"cookies": [_cookie("sid", "a.test")], "origins": []}
    assert looks_signed_in(state, origin="https://a.test") is True


def test_the_number_of_cookies_is_bounded() -> None:
    """One site cannot make a saved login into a row nothing can read back."""
    state = {
        "cookies": [_cookie(f"c{i}", "a.test") for i in range(5000)],
        "origins": [],
    }
    assert len(scope_state(state, origin="https://a.test")["cookies"]) <= 200


def test_malformed_input_is_survived_rather_than_trusted() -> None:
    for state in ({}, {"cookies": "nope"}, {"cookies": [None, 3, "x"]}):
        assert scope_state(state, origin="https://a.test") == {
            "cookies": [],
            "origins": [],
        }


def test_the_host_is_read_without_the_port() -> None:
    assert host_of("https://app.example.com:8443") == "app.example.com"
    assert host_of("app.example.com") == "app.example.com"
