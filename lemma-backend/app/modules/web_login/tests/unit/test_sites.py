"""Reducing a host or an origin to the site a person would name.

The survivors of `scope.py`, and the only guessing this module still does --
which is why they are worth pinning down. Everything that decided "is this a
login" from cookie shapes is gone; grouping `api.asur.work` under `asur.work`
is a public-suffix fact, not a judgement.
"""

from __future__ import annotations

from app.modules.web_login.services.sites import (
    same_site,
    site_from_origin,
    site_of,
)


class TestTheSiteAHostBelongsTo:
    def test_a_subdomain_groups_under_its_registrable_domain(self) -> None:
        """The whole reason this exists: `asur.work` and `api.asur.work` are
        one login to a person, and listing them separately offers to forget
        half of one -- the half nobody visited on purpose."""
        assert site_of("api.asur.work") == "asur.work"
        assert site_of("asur.work") == "asur.work"

    def test_a_host_with_no_registrable_domain_has_no_site(self) -> None:
        """Empty, not the host itself. Callers decide what to do with that,
        and they differ: the listing falls back to the host, and grouping
        every such host under `""` would collapse them into one row."""
        assert site_of("localhost") == ""
        assert site_of("127.0.0.1") == ""

    def test_nothing_is_not_a_site(self) -> None:
        assert site_of("") == ""


class TestWhetherTwoHostsAreOneSite:
    def test_a_subdomain_matches_its_site(self) -> None:
        assert same_site("api.asur.work", "asur.work")

    def test_an_exact_match_is_the_fallback_where_there_is_no_site(self) -> None:
        """`localhost` and a bare IP have no registrable domain, so the only
        honest reading is an exact match -- otherwise every one of them would
        be "the same site" as every other."""
        assert same_site("127.0.0.1", "127.0.0.1")
        assert not same_site("127.0.0.1", "localhost")


class TestTheSiteAnOriginBelongsTo:
    """An origin and a cookie's domain have to reduce to the same string.

    They arrive in different shapes -- an origin carries a scheme and often a
    port, a cookie's domain carries neither -- and forgetting a site matches
    its cookies by comparing the two. When they disagreed, the delete reported
    "nothing to forget" about a site the browser was plainly signed in to.
    """

    def test_an_origin_reduces_to_what_the_cookie_list_shows(self) -> None:
        assert site_from_origin("https://api.asur.work/account") == "asur.work"
        assert site_from_origin("asur.work") == "asur.work"

    def test_a_bare_ip_loses_its_port(self) -> None:
        """Caught by the sign-in e2e, which serves its site on a port.
        `site_of` is empty for an IP and the caller fell back to the *whole
        origin*, so `127.0.0.1` never matched `http://127.0.0.1:18099` and
        the sign-out silently did nothing.

        The port goes because a cookie is not scoped by port -- one set for a
        host is sent to every port on it -- so `:18099` names nothing a person
        could forget on its own."""
        assert site_from_origin("http://127.0.0.1:18099") == "127.0.0.1"

    def test_localhost_is_its_own_site(self) -> None:
        assert site_from_origin("http://localhost:3000") == "localhost"

    def test_an_ipv6_literal_is_not_cut_at_its_own_colons(self) -> None:
        """The brackets are what tell a port apart from the address."""
        assert site_from_origin("http://[::1]:8080") == "::1"
