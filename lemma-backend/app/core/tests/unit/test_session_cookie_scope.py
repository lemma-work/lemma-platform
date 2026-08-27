"""The refresh cookie has to reach both places a session is refreshed from."""

import pytest

from app.core.api.session_cookie_scope import widen_refresh_cookie_path
from app.core.config import settings
from app.core.runtime_config import APP_ORIGIN_API_URL, app_api_url

pytestmark = pytest.mark.unit

# What SuperTokens actually emits, copied from a live response rather than
# imagined: the attribute order and casing here are the thing being matched.
REFRESH = (
    "sRefreshToken=abc.def.ghi; Domain=.lemma.localhost; "
    "expires=Fri, 04 Dec 2026 06:26:29 GMT; HttpOnly; "
    "Path=/st/auth/session/refresh; SameSite=lax"
)
ACCESS = (
    "sAccessToken=abc.def.ghi; Domain=.lemma.localhost; "
    "expires=Thu, 26 Aug 2027 06:26:28 GMT; HttpOnly; Path=/; SameSite=lax"
)


def test_the_refresh_cookie_reaches_an_apps_prefixed_refresh_path():
    # The bug: an app's SDK refreshes at `/_lemma/st/auth/session/refresh`,
    # which the original Path does not cover, so the request went out with no
    # cookie and the app signed itself out about an hour after it loaded.
    widened = widen_refresh_cookie_path(REFRESH)
    assert "Path=/;" in widened or widened.endswith("Path=/")
    assert "Path=/st/auth/session/refresh" not in widened
    # Everything else about the cookie is left exactly as it was.
    for attribute in ("HttpOnly", "SameSite=lax", "Domain=.lemma.localhost"):
        assert attribute in widened


def test_no_other_cookie_is_touched():
    # Widening is a deliberate, narrow concession for one cookie. A rewrite that
    # caught others would be quietly changing the scope of credentials nobody
    # was asking about.
    assert widen_refresh_cookie_path(ACCESS) == ACCESS
    assert widen_refresh_cookie_path("other=1; Path=/narrow") == "other=1; Path=/narrow"


def test_a_refresh_cookie_with_no_path_is_left_alone():
    # Absent means "the current directory" to a browser, which is a different
    # thing from `/`. Inventing one here would be a silent behaviour change.
    bare = "sRefreshToken=abc; HttpOnly; SameSite=lax"
    assert widen_refresh_cookie_path(bare) == bare


def test_apps_are_only_sent_to_their_own_origin_where_that_is_needed(monkeypatch):
    # On a real domain the app subdomain and the API host share a registrable
    # domain, so an app's cross-origin calls are already same-site and carry the
    # session. Turning this on there would widen the refresh cookie for nothing.
    monkeypatch.setattr(settings, "app_api_via_app_origin", False)
    assert app_api_url() is None

    monkeypatch.setattr(settings, "app_api_via_app_origin", True)
    assert app_api_url() == APP_ORIGIN_API_URL


def test_a_blank_older_cookie_domain_is_kept_rather_than_folded_to_none():
    """`older_cookie_domain=""` is a value, not an absent one.

    SuperTokens reads the empty string as "the cookies being replaced were
    host-only" and clears them; `None` means "nothing to replace" and clears
    nothing. Desktop is making exactly that migration -- v0.7.0 rendered
    `SESSION_COOKIE_DOMAIN` empty, main renders `.lemma.localhost` -- so an
    install that crossed it holds both cookies, sends both, and SuperTokens
    answers the refresh 500 with `The request contains multiple session
    cookies`. The SDK retries a 500 per query, for ever.

    Its neighbours in `config.py` *do* fold blank to None, which is why this is
    asserted rather than left to a future tidy-up: making this field consistent
    with them is the one edit that silently un-fixes the loop.
    """
    from app.core.config import Settings

    assert Settings(session_cookie_older_domain="").session_cookie_older_domain == ""
    assert Settings().session_cookie_older_domain is None
    # ...while the neighbour it sits beside keeps folding blank to None.
    assert Settings(session_cookie_domain="").session_cookie_domain is None


# What SuperTokens sends to delete a refresh cookie, copied from the library's
# own `set_cookie(..., value="", expires=0)` shape rather than imagined.
CLEARING = (
    'sRefreshToken=""; expires=Thu, 01 Jan 1970 00:00:00 GMT; '
    "HttpOnly; Path=/st/auth/session/refresh; SameSite=lax"
)


def test_a_refresh_cookie_being_deleted_keeps_the_path_it_lives_at():
    """Widening a clearing cookie makes it match nothing.

    Cookie removal is an exact name+domain+path match. `older_cookie_domain`
    clears the stale pair by re-sending the refresh cookie empty at SuperTokens'
    narrow `refresh_token_path` -- so rewriting that to `/`, as this middleware
    does for live cookies, means the doomed cookie is never actually removed.

    Which is worse than doing nothing: the duplicate pair survives, but the
    refresh stops answering 500 and starts answering 200 with no `front-token`.
    The retry loop continues and no longer says why.
    """
    assert widen_refresh_cookie_path(CLEARING) == CLEARING
    # A bare `name=;` spelling is the same cookie being deleted.
    bare = CLEARING.replace('sRefreshToken=""', "sRefreshToken=")
    assert widen_refresh_cookie_path(bare) == bare


def test_a_live_refresh_cookie_is_still_widened():
    # The clearing exception must not cost the fix it sits inside: an app's SDK
    # refreshes at `/_lemma/st/auth/session/refresh`, which the narrow path
    # does not cover.
    assert "Path=/;" in widen_refresh_cookie_path(REFRESH)
