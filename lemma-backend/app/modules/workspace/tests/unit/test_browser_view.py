"""The browser view socket, and the bridge underneath it.

The previous version of this feature had no test that opened a socket at all,
which is how it shipped with no Origin check and an unbounded frame size. These
open sockets.
"""

from __future__ import annotations

import pytest

from app.modules.workspace.api.controllers import browser_view_controller as view
from app.modules.workspace.services.ws_bridge import (
    MAX_FRAME_BYTES,
    origin_is_allowed,
)


# ---------------------------------------------------------------------------
# Origin
# ---------------------------------------------------------------------------


def test_a_page_on_another_site_cannot_open_the_socket() -> None:
    """Browsers do not apply same-origin to WebSockets, but do send cookies.

    So without this check any page the person visits could open this socket as
    them, watch their screen, and type into it.
    """
    allowed = ("https://app.lemma.test", "https://api.lemma.test")
    assert origin_is_allowed("https://evil.test", allowed=allowed) is False
    assert (
        origin_is_allowed("https://app.lemma.test.evil.test", allowed=allowed) is False
    )
    assert origin_is_allowed("https://app.lemma.test", allowed=allowed) is True


def test_a_trailing_slash_or_case_does_not_decide_the_answer() -> None:
    allowed = ("https://app.lemma.test/",)
    assert origin_is_allowed("https://APP.lemma.test", allowed=allowed) is True


def test_a_client_that_sends_no_origin_is_allowed() -> None:
    """Which is every non-browser client: the CLI, the SDK, and the tests.

    A browser always sends one, and page script cannot forge it -- that is what
    makes checking a present one worth anything.
    """
    assert origin_is_allowed(None, allowed=("https://app.lemma.test",)) is True


def test_frames_are_bounded() -> None:
    """`max_size=None` means one frame from a process the agent controls is
    buffered whole in the API's memory."""
    assert 0 < MAX_FRAME_BYTES <= 16 * 1024 * 1024


# ---------------------------------------------------------------------------
# The socket's refusals
# ---------------------------------------------------------------------------


class _FakeService:
    def __init__(self, *, fail: Exception | None = None) -> None:
        self.fail = fail
        self.opened: list[dict] = []
        self.closed = False

    async def open_session(self, user_id, *, mode, origin=None, session=None):
        self.opened.append({"user_id": user_id, "mode": mode, "origin": origin})
        if self.fail is not None:
            raise self.fail
        return "ws://sandbox.test/session?target=t1", {"X-Lemma-Relay-Token": "t"}

    async def status(self, user_id):
        return {"state": "stopped"}

    async def close(self) -> None:
        self.closed = True


def _client(monkeypatch, service: _FakeService):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setattr(view, "BrowserViewService", lambda: service)
    monkeypatch.setattr(view, "_allowed_origins", lambda: ("https://app.lemma.test",))
    app = FastAPI()
    app.include_router(view.router)
    return TestClient(app)


def test_a_socket_from_a_foreign_origin_is_closed_before_it_is_accepted(
    monkeypatch,
) -> None:
    service = _FakeService()
    client = _client(monkeypatch, service)
    with pytest.raises(Exception):  # noqa: B017 - any refusal, no accepted socket
        with client.websocket_connect(
            "/workspace/browser/view", headers={"Origin": "https://evil.test"}
        ):
            pass
    assert service.opened == [], "nothing was reached for on a refused origin"


def test_a_socket_with_no_session_is_closed_unauthenticated(monkeypatch) -> None:
    service = _FakeService()
    client = _client(monkeypatch, service)
    with pytest.raises(Exception):
        with client.websocket_connect("/workspace/browser/view"):
            pass
    assert service.opened == [], "no sandbox was touched for an unauthenticated caller"


def test_the_close_codes_are_distinct(monkeypatch) -> None:
    """Each maps to a different sentence and a different remedy: sign in again,
    wake the computer, replace the image, use another kind of computer."""
    codes = {
        view.CLOSE_UNAUTHENTICATED,
        view.CLOSE_ORIGIN_REFUSED,
        view.CLOSE_NO_BROWSER,
        view.CLOSE_UNSUPPORTED,
        view.CLOSE_RELAY_ABSENT,
    }
    assert len(codes) == 5
    assert all(4000 <= code < 5000 for code in codes)


def test_the_allowlisted_path_matches_the_route() -> None:
    """The security layer lets this handshake through by path, so a rename that
    misses one of the two leaves the socket either unreachable or unguarded."""
    from app.core.security import EXCLUDED_PATHS

    assert view.BROWSER_VIEW_WS_PATH in EXCLUDED_PATHS
    routes = {getattr(r, "path", "") for r in view.router.routes}
    assert "/workspace/browser/view" in routes
