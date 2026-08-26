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
