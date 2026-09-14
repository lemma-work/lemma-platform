"""The browser view socket, and the bridge underneath it.

The previous version of this feature had no test that opened a socket at all,
which is how it shipped with no Origin check and an unbounded frame size. These
open sockets.
"""

from __future__ import annotations


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


def _client(service: _FakeService):
    """The real router, with its collaborators supplied rather than patched.

    Overriding a dependency is injection; reaching into the module and
    replacing `BrowserViewService` would be putting a double *inside* the
    subject, which survives a rename that should have failed the test.
    """
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(view.router)
    app.dependency_overrides[view.browser_view_service] = lambda: service
    app.dependency_overrides[view.allowed_origins] = lambda: ("https://app.lemma.test",)
    return TestClient(app)


def test_a_socket_from_a_foreign_origin_is_refused_with_its_own_code() -> None:
    """Refused, and told which refusal it was.

    These two used to assert only that *something* went wrong and that no
    sandbox was touched, which both held while the pane was being lied to. A
    close sent before `accept()` never carries its code: ASGI turns it into a
    rejected handshake and page script sees 1006, the anonymous "abnormal
    closure". So every refusal reached the person as "The connection dropped.
    Reconnecting.", and the client retried refusals that could never succeed.

    Asserting the delivered code is what makes that visible, so that is what
    these assert now.
    """
    service = _FakeService()
    client = _client(service)
    with client.websocket_connect(
        "/workspace/browser/view", headers={"Origin": "https://evil.test"}
    ) as socket:
        refusal = socket.receive()
    assert refusal["type"] == "websocket.close"
    assert refusal["code"] == view.CLOSE_ORIGIN_REFUSED
    assert service.opened == [], "nothing was reached for on a refused origin"


def test_a_socket_with_no_session_is_refused_unauthenticated() -> None:
    service = _FakeService()
    client = _client(service)
    with client.websocket_connect("/workspace/browser/view") as socket:
        refusal = socket.receive()
    assert refusal["type"] == "websocket.close"
    assert refusal["code"] == view.CLOSE_UNAUTHENTICATED
    assert service.opened == [], "no sandbox was touched for an unauthenticated caller"


def test_the_close_codes_are_distinct() -> None:
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
