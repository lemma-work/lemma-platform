"""The browser view socket, and the bridge underneath it.

The previous version of this feature had no test that opened a socket at all,
which is how it shipped with no Origin check and an unbounded frame size. These
open sockets.
"""

from __future__ import annotations

from uuid import uuid4

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

    async def open_vnc_session(
        self, user_id, *, mode, origin=None, conversation_id=None
    ):
        self.opened.append(
            {
                "user_id": user_id,
                "mode": mode,
                "origin": origin,
                "conversation_id": conversation_id,
            }
        )
        if self.fail is not None:
            raise self.fail
        return "ws://sandbox.test/vnc?mode=view", {"X-Lemma-Relay-Token": "t"}

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


# ---------------------------------------------------------------------------
# Where a session may be put
# ---------------------------------------------------------------------------


class _Relay:
    """A relay whose address is on the internet, or is not."""

    def __init__(self, *, public: bool) -> None:
        self.public = public
        self.loaded: list[dict] = []

    async def endpoint_is_public(self) -> bool:
        return self.public

    async def load_state(self, state, *, domain) -> None:
        self.loaded.append({"state": state, "domain": domain})


async def test_a_saved_login_is_not_loaded_into_a_publicly_reachable_sandbox() -> None:
    """On E2B every published port is a public name.

    New sandboxes are created with public traffic off and answer 403 without a
    token, but that is fixed at create -- one made before the flag existed stays
    open for life. `reach_port` has always reported which kind it is and nothing
    asked.

    It matters here because of what else is in that sandbox: the agent-browser
    dashboard on `0.0.0.0:4848` with nothing in front of it, and it is not the
    passive viewer it was once described as -- it lists the browser's cookies
    and evaluates script. Putting somebody's session behind that is handing
    over their account.
    """
    from app.modules.workspace.services import browser_view_service as module

    relay = _Relay(public=True)

    with pytest.raises(module.BrowserRelayUnavailable) as refused:
        await module._require_private(relay, doing="load a saved login")

    assert "reachable from the internet" in str(refused.value)
    assert relay.loaded == []


async def test_a_private_sandbox_is_used_without_complaint() -> None:
    from app.modules.workspace.services import browser_view_service as module

    await module._require_private(_Relay(public=False), doing="load a saved login")


async def test_a_relay_that_cannot_say_is_not_treated_as_public() -> None:
    """Refusing on a failed probe would lock people out of a working sandbox.

    The permissive answer is safe here only because the paths that could
    actually leak run this same check when *they* run.
    """
    from app.modules.workspace.services import browser_view_service as module

    class _Unreachable(_Relay):
        async def endpoint_is_public(self) -> bool:
            raise OSError("no route to the sandbox")

    await module._require_private(_Unreachable(public=True), doing="sign in to a site")


# ---------------------------------------------------------------------------
# Which session a plain watch/drive lands in
# ---------------------------------------------------------------------------


class _VncRelay(_Relay):
    """A relay that remembers what `ensure_browser` and `vnc_socket_url` were
    asked for, without touching a real sandbox."""

    def __init__(self) -> None:
        super().__init__(public=False)
        self.ensured: dict | None = None

    async def ensure_browser(self, *, origin, session, domain):
        self.ensured = {"origin": origin, "session": session, "domain": domain}
        return {"session": session or "workspace"}

    async def vnc_socket_url(self, *, mode, session):
        return f"ws://sandbox.test/vnc?mode={mode}&session={session}", {}


def _service_with_relay(relay: _VncRelay):
    """`BrowserViewService`, its own sandbox resolution replaced with `relay`.

    `_relay` is the one method here that touches a real sandbox -- everything
    `open_vnc_session` decides afterwards is what this test is about, so that
    is the seam, not a double planted inside `open_vnc_session` itself.
    """
    from app.modules.workspace.services import browser_view_service as module

    class _Service(module.BrowserViewService):
        async def _relay(self, user_id, *, start):
            return relay

    return _Service()


async def test_no_caller_names_a_browser_session() -> None:
    """There is one browser per sandbox, so nothing picks between them.

    A conversation used to select `agent_session(conversation_id)`, a Chrome
    and profile of its own -- which is exactly what forced a sign-in to be
    captured in one browser and rebuilt in another, and the rebuilding is what
    kept being wrong. The conversation id is still accepted, for logging and
    the keepalive; it must no longer steer anything.
    """
    relay = _VncRelay()
    await _service_with_relay(relay).open_vnc_session(
        uuid4(), mode="view", conversation_id=uuid4()
    )
    assert relay.ensured == {"origin": None, "session": None, "domain": None}


async def test_a_plain_watch_with_no_conversation_lands_in_the_same_browser() -> None:
    relay = _VncRelay()
    await _service_with_relay(relay).open_vnc_session(uuid4(), mode="view")
    assert relay.ensured == {"origin": None, "session": None, "domain": None}


async def test_a_sign_in_steers_the_one_browser_rather_than_opening_another() -> None:
    """An origin still steers -- it just does so in the browser everything
    else is already using, instead of a session named for the site."""

    relay = _VncRelay()
    await _service_with_relay(relay).open_vnc_session(
        uuid4(),
        mode="view",
        origin="https://example.com",
        conversation_id=uuid4(),
    )
    assert relay.ensured == {
        "origin": "https://example.com",
        "session": None,
        "domain": "example.com",
    }


# ---------------------------------------------------------------------------
# Hanging up
# ---------------------------------------------------------------------------


class _WebSocketThatIsAlreadyGone:
    """A socket whose handshake never completed, which is what production had.

    `close()` raises `AttributeError` from inside uvicorn's own close path --
    `'WebSocketProtocol' object has no attribute 'transfer_data_task'` -- when
    the client went away before the refusal was written. Reproduced by type
    rather than by message: the point is that it is not a `RuntimeError`.
    """

    async def accept(self) -> None:
        self.accepted = True

    def __init__(self, raises: BaseException | None = None) -> None:  # noqa: F811
        self.accepted = False
        self.close_attempts = 0
        self.raises = raises or AttributeError(
            "'WebSocketProtocol' object has no attribute 'transfer_data_task'"
        )

    async def close(self, code: int) -> None:
        self.close_attempts += 1
        raise self.raises


@pytest.mark.asyncio
async def test_a_refusal_that_cannot_be_delivered_is_not_an_error_of_its_own() -> None:
    """The close path guessed `RuntimeError` and got `AttributeError`.

    So refusing a socket whose client had already gone raised, uvicorn logged
    "Exception in ASGI application", and the pane -- which saw an error instead
    of its close code -- reconnected and asked again. "The browser is not
    running" is the ordinary resting state of an idle workspace, and it was
    reaching people as a crash loop.
    """
    socket = _WebSocketThatIsAlreadyGone()

    await view._refuse(socket, view.CLOSE_NO_BROWSER)

    assert socket.accepted, "a close before accept never carries its code"
    assert socket.close_attempts == 1, "the refusal was attempted"


@pytest.mark.asyncio
async def test_collecting_the_keep_awake_task_does_not_raise() -> None:
    """Every close of the browser pane logged an unhandled ASGI error.

    The keep-awake task is cancelled when the socket ends and then awaited, so
    that a cancelled task is collected rather than outliving the request. That
    await is *guaranteed* to raise `CancelledError` -- and it was collected
    under `suppress(Exception)`, which does not catch it, because
    `CancelledError` is a `BaseException`. So uvicorn logged a stack trace for
    the ordinary act of stopping watching.

    A real task, really cancelled: the bug was entirely in which exception the
    suppression named, so a stand-in that raised something else would have
    proved nothing.
    """
    import asyncio

    async def _forever() -> None:
        await asyncio.sleep(3600)

    task = asyncio.get_running_loop().create_task(_forever())
    await asyncio.sleep(0)  # let it reach the sleep

    await view._collect(task)

    assert task.cancelled(), "collected means finished, not merely asked to stop"


@pytest.mark.asyncio
async def test_every_way_a_socket_is_seen_to_end_is_handled() -> None:
    """The set has been corrected twice from production; this is what pins it.

    `RuntimeError` was the original guess. `AttributeError` was found crashing
    refusals in dev. `WebSocketDisconnect` was found crashing them again in a
    local run against E2B that was meant to confirm the first fix -- and it is
    the *ordinary* case, because by the time a refusal is written the person may
    simply have navigated away.
    """
    from fastapi import WebSocketDisconnect

    for failure in (
        RuntimeError("socket is not connected"),
        AttributeError("'WebSocketProtocol' object has no attribute ..."),
        OSError("transport gone"),
        WebSocketDisconnect(code=1006),
    ):
        socket = _WebSocketThatIsAlreadyGone(failure)
        await view._refuse(socket, view.CLOSE_NO_BROWSER)
        assert socket.close_attempts == 1, f"{type(failure).__name__} was not handled"
