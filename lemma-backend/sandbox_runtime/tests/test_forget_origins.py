"""Which origins "sign me out of this site" actually clears.

The rule is not obvious and getting it wrong was invisible: a cookie matches
by domain and records no port, while local storage is keyed by the full
origin. Send only bare hosts and the cookie goes while the storage stays --
which is exactly what shipped, because the helper written to add the
port-bearing origins was never called.

Measured on a real browser against `http://127.0.0.1:18099`, the origin the
e2e suite serves and the one the original investigation was testing against:

    clear `http://127.0.0.1`        -> cookie gone, localStorage kept
    clear `http://127.0.0.1:18099`  -> cookie gone, localStorage gone
"""

from __future__ import annotations

from sandbox_runtime.browser_relay.cookies import origins_to_clear


class TestBothSchemesOnTheBareHost:
    """What a cookie needs. `Secure` only says it was https *somewhere*."""

    def test_a_host_with_no_page_open_still_gets_both_schemes(self) -> None:
        assert origins_to_clear({"example.com"}, []) == {
            "https://example.com",
            "http://example.com",
        }

    def test_several_hosts_each_get_both(self) -> None:
        found = origins_to_clear({"a.test", "b.test"}, [])
        assert found == {
            "https://a.test",
            "http://a.test",
            "https://b.test",
            "http://b.test",
        }


class TestTheFullOriginOfAnyOpenPage:
    """What site storage needs, and the half that was missing."""

    def test_an_open_page_on_a_port_contributes_its_origin(self) -> None:
        found = origins_to_clear(
            {"127.0.0.1"}, [{"url": "http://127.0.0.1:18099/probe"}]
        )

        assert "http://127.0.0.1:18099" in found, (
            "local storage is keyed by full origin; without the port nothing "
            "clears it, which is the bug this function exists to close"
        )
        # The bare host stays too: the cookie is matched by domain.
        assert {"http://127.0.0.1", "https://127.0.0.1"} <= found

    def test_a_page_on_another_host_is_not_dragged_in(self) -> None:
        found = origins_to_clear(
            {"example.com"},
            [{"url": "https://unrelated.test/"}, {"url": "https://example.com/x"}],
        )

        assert found == {"https://example.com", "http://example.com"}

    def test_a_default_port_page_adds_nothing_new(self) -> None:
        """The ordinary case, and why this went unnoticed: for a site on 443
        the full origin and the bare host are the same string."""
        assert origins_to_clear(
            {"example.com"}, [{"url": "https://example.com/article"}]
        ) == {"https://example.com", "http://example.com"}

    def test_targets_that_are_not_pages_are_ignored(self) -> None:
        """`chrome://newtab`, `devtools://`, a blank tab, and a target whose
        url key is missing entirely -- a real target list has all of these."""
        found = origins_to_clear(
            {"example.com"},
            [
                {"url": "chrome://newtab/"},
                {"url": "about:blank"},
                {"url": "devtools://devtools/bundled/x.html"},
                {"url": ""},
                {},
            ],
        )

        assert found == {"https://example.com", "http://example.com"}


class TestNothingToDo:
    def test_no_hosts_means_no_origins(self) -> None:
        """Not "clear everything". An empty set here used to be the shape of
        the accident where a filter matched nothing and the caller cleared
        the whole jar."""
        assert origins_to_clear(set(), [{"url": "https://example.com/"}]) == set()
