"""The impersonating fetch path.

`web_fetch` reads pages from the backend process, through a client that replays
a browser TLS fingerprint. That makes it a server-side fetcher pointed at a URL
the model chose, which is the same shape as the connector case in
`test_url_guard.py` and needs the same guarantees: every hop re-validated, and a
body that cannot grow without bound.

The guard logic itself is tested there. What is tested here is that this client
actually routes through it, because the failure mode is silent — a redirect
followed inside libcurl would never reach `assert_safe_url` at all.
"""

from __future__ import annotations

import pytest

from app.core.net import impersonating_client
from app.core.net.impersonating_client import (
    HttpStatusError,
    close_impersonating_client,
    fetch_guarded_impersonated,
    get_impersonating_client,
)
from app.core.net.url_guard import UnsafeUrlError

pytestmark = [pytest.mark.unit, pytest.mark.asyncio]


class _FakeResponse:
    def __init__(self, status_code: int, headers: dict, chunks: list[bytes]):
        self.status_code = status_code
        self.headers = headers
        self._chunks = chunks
        self.consumed = 0

    async def aiter_content(self):
        for chunk in self._chunks:
            self.consumed += 1
            yield chunk


class _FakeSession:
    """Stands in for `curl_cffi.requests.AsyncSession`.

    Records every URL it was asked for, so a test can assert that a hop the
    guard should have refused was never actually requested.
    """

    def __init__(self, responses: dict[str, _FakeResponse]):
        self._responses = responses
        self.requested: list[str] = []
        self.last_response: _FakeResponse | None = None

    def stream(self, method: str, url: str, **kwargs):
        self.requested.append(url)
        response = self._responses.get(url) or _FakeResponse(200, {}, [b"<html/>"])
        self.last_response = response

        class _Ctx:
            async def __aenter__(self_inner):
                return response

            async def __aexit__(self_inner, *exc):
                return False

        return _Ctx()


def _install(monkeypatch, session: _FakeSession) -> None:
    monkeypatch.setattr(
        impersonating_client, "get_impersonating_client", lambda: session
    )


def _redirect(location: str) -> _FakeResponse:
    return _FakeResponse(302, {"location": location}, [])


class TestRedirectsAreRevalidated:
    async def test_a_redirect_into_the_private_network_is_refused(self, monkeypatch):
        """The whole reason redirects are followed here rather than in libcurl:
        a public URL that answers `302 -> 10.0.0.5` is how a fetcher gets walked
        onto the internal network."""
        session = _FakeSession(
            {"https://example.com/a": _redirect("http://10.0.0.5/admin")}
        )
        _install(monkeypatch, session)

        with pytest.raises(UnsafeUrlError) as excinfo:
            await fetch_guarded_impersonated(
                "https://example.com/a", max_bytes=1000, timeout=5
            )

        assert excinfo.value.reason == "private_address"
        # The refusal has to happen before the request, not after it.
        assert session.requested == ["https://example.com/a"]

    async def test_the_metadata_service_is_refused_through_a_redirect(
        self, monkeypatch
    ):
        session = _FakeSession(
            {"https://example.com/a": _redirect("http://169.254.169.254/latest/")}
        )
        _install(monkeypatch, session)

        with pytest.raises(UnsafeUrlError) as excinfo:
            await fetch_guarded_impersonated(
                "https://example.com/a", max_bytes=1000, timeout=5
            )
        assert excinfo.value.reason == "link_local_address"

    async def test_a_relative_redirect_is_resolved_against_the_current_hop(
        self, monkeypatch
    ):
        """`Location` is allowed to be relative, and a naive implementation
        would validate the relative string and then request something else."""
        session = _FakeSession(
            {
                "https://example.com/a": _redirect("/b"),
                "https://example.com/b": _FakeResponse(
                    200, {"content-type": "text/html"}, [b"<html>ok</html>"]
                ),
            }
        )
        _install(monkeypatch, session)

        result = await fetch_guarded_impersonated(
            "https://example.com/a", max_bytes=1000, timeout=5
        )

        assert result.body == b"<html>ok</html>"
        assert result.final_url == "https://example.com/b"
        assert session.requested == ["https://example.com/a", "https://example.com/b"]

    async def test_a_redirect_without_a_location_is_refused(self, monkeypatch):
        session = _FakeSession({"https://example.com/a": _FakeResponse(302, {}, [])})
        _install(monkeypatch, session)

        with pytest.raises(UnsafeUrlError) as excinfo:
            await fetch_guarded_impersonated(
                "https://example.com/a", max_bytes=1000, timeout=5
            )
        assert excinfo.value.reason == "invalid_redirect"

    async def test_the_hop_limit_is_enforced(self, monkeypatch):
        session = _FakeSession(
            {
                "https://example.com/a": _redirect("https://example.com/b"),
                "https://example.com/b": _redirect("https://example.com/c"),
                "https://example.com/c": _redirect("https://example.com/d"),
                "https://example.com/d": _redirect("https://example.com/e"),
            }
        )
        _install(monkeypatch, session)

        with pytest.raises(UnsafeUrlError) as excinfo:
            await fetch_guarded_impersonated(
                "https://example.com/a", max_bytes=1000, timeout=5, max_redirects=3
            )
        assert excinfo.value.reason == "too_many_redirects"


class TestTheBodyIsBounded:
    async def test_the_body_is_cut_off_during_the_transfer(self, monkeypatch):
        """A host that advertises a small response and sends a large one should
        cost us `max_bytes`, not its whole body — so the cap has to stop the
        iteration, not measure what was already buffered."""
        chunks = [b"x" * 100] * 50
        response = _FakeResponse(200, {"content-type": "text/html"}, chunks)
        session = _FakeSession({"https://example.com/big": response})
        _install(monkeypatch, session)

        with pytest.raises(UnsafeUrlError) as excinfo:
            await fetch_guarded_impersonated(
                "https://example.com/big", max_bytes=500, timeout=5
            )

        assert excinfo.value.reason == "response_too_large"
        # Stopped early rather than reading all fifty chunks.
        assert response.consumed == 6
        assert response.consumed < len(chunks)

    async def test_a_body_within_the_cap_is_returned_whole(self, monkeypatch):
        session = _FakeSession(
            {
                "https://example.com/ok": _FakeResponse(
                    200, {"content-type": "text/html; charset=utf-8"}, [b"ab", b"cd"]
                )
            }
        )
        _install(monkeypatch, session)

        result = await fetch_guarded_impersonated(
            "https://example.com/ok", max_bytes=1000, timeout=5
        )

        assert result.body == b"abcd"
        assert result.content_type == "text/html; charset=utf-8"


class TestStatusHandling:
    async def test_a_refused_page_reports_its_status(self, monkeypatch):
        """The caller turns this into "escalate to the browser", so the status
        has to survive rather than becoming a generic transport error."""
        session = _FakeSession({"https://example.com/x": _FakeResponse(403, {}, [])})
        _install(monkeypatch, session)

        with pytest.raises(HttpStatusError) as excinfo:
            await fetch_guarded_impersonated(
                "https://example.com/x", max_bytes=1000, timeout=5
            )
        assert excinfo.value.status_code == 403
        assert "403" in str(excinfo.value)


class TestTheClientIsProcessWide:
    async def test_the_session_is_built_once(self):
        """`check_io_hygiene`'s process-lifetime rule in test form: building a
        libcurl session per call would throw away every kept-alive connection."""
        try:
            first = get_impersonating_client()
            second = get_impersonating_client()
            assert first is second
        finally:
            # This is the one test that builds the real thing; leaving it open
            # leaks a libcurl handle into the rest of the suite.
            await close_impersonating_client()
