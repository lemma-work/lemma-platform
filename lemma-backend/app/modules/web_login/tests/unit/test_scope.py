"""Narrowing a captured browser state to the site it belongs to.

The first version of this feature captured the whole shared browser profile --
every site the agent had ever visited -- and stored it under one site's name, to
be restored whenever any agent asked for that site. These are the tests that
make that impossible.
"""

from __future__ import annotations

from app.modules.web_login.services.scope import (
    same_site,
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


def test_the_sites_api_host_is_kept() -> None:
    """The bug this whole rule was widened for.

    A login is not one host. Lemma's own deployment serves its app on
    `asur.work` and its API on `api.asur.work`, and SuperTokens sets the
    HttpOnly pair that *is* the session -- `sAccessToken`, `sRefreshToken` --
    from the API host. Keeping only what the website host had set stored
    `sFrontToken` and a timestamp: exactly the cookies the frontend SDK reads
    to decide a session exists. So the restored browser believed it was signed
    in, got a 401, bounced to the login form, and the agent asked again.
    """
    state = {
        "cookies": [
            _cookie("sFrontToken", "asur.work"),
            _cookie("sAccessToken", "api.asur.work"),
            _cookie("sRefreshToken", "api.asur.work"),
            _cookie("SID", "accounts.google.com"),
        ],
        "origins": [],
    }

    kept = [
        c["name"] for c in scope_state(state, origin="https://asur.work")["cookies"]
    ]

    assert kept == ["sFrontToken", "sAccessToken", "sRefreshToken"]


def test_another_tenant_on_shared_hosting_is_still_a_different_site() -> None:
    """Why the public suffix list has to be real, and its private half on.

    "The last two labels" would make every GitHub Pages site one site, so a
    login saved for one would restore another's cookies. `github.io` is a
    suffix only in the list's private section.
    """
    state = {"cookies": [_cookie("theirs", "b.github.io")], "origins": []}
    assert scope_state(state, origin="https://a.github.io")["cookies"] == []

    assert same_site("api.example.co.uk", "app.example.co.uk") is True
    assert same_site("y.herokuapp.com", "x.herokuapp.com") is False


def test_a_host_with_no_registrable_domain_matches_only_itself() -> None:
    """`localhost` and bare IPs have no "rest of the site" to belong to, so
    widening there would put every single-label name in one bucket."""
    assert same_site("localhost", "localhost") is True
    assert same_site("otherhost", "localhost") is False
    assert same_site("127.0.0.2", "127.0.0.1") is False


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
