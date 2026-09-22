"""The browser view socket, and the bridge underneath it.

The previous version of this feature had no test that opened a socket at all,
which is how it shipped with no Origin check and an unbounded frame size. These
open sockets.
"""

from __future__ import annotations

import asyncio
from uuid import uuid4

import pytest


from app.modules.workspace.api.controllers import browser_view_controller as view
from app.modules.workspace.api.controllers.browser_view_session import (
    user_id_resolver,
)
from app.modules.workspace.services.ws_bridge import (
    origins_from,
    MAX_FRAME_BYTES,
    origin_is_allowed,
    origin_refusal_hint,
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


class TestTheListIsTheOneTheRestOfTheAppUses:
    """The refusal that cost somebody a sign-in.

    `browser_sign_in` hands a person the wheel through this socket. Watched
    in production: the app was served on an apex host and a `www.` host,
    both configured in `cors_origins`, while this allowlist was built from
    `frontend_url` / `api_url` / `auth_frontend_url` alone and permitted a
    third host only. Six refusals in two minutes, no screen for the person,
    and the agent reported that they had declined to sign in.
    """

    #: The deployment that failed: the app served on an apex and a `www.`
    #: host, both configured, while `frontend_url` named a third and
    #: `auth_frontend_url` carried a path.
    CONFIGURED = (
        "https://lemma.test",
        "https://www.lemma.test",
        "http://localhost:3000",
    )
    URLS = (
        "https://train.lemma.test/",
        "https://api.lemma.test",
        "https://train.lemma.test/auth",
    )

    def _allowed(self) -> tuple[str, ...]:
        # The pure builder, not a patched `settings`: a double inside the
        # subject would survive a rename of the settings this is about.
        return origins_from(self.CONFIGURED, *self.URLS)

    def test_every_host_the_app_is_served_on_can_watch(self) -> None:
        allowed = self._allowed()

        for origin in (
            "https://lemma.test",
            "https://www.lemma.test",
            "https://train.lemma.test",
            "http://localhost:3000",
        ):
            assert origin_is_allowed(origin, allowed=allowed) is True, origin

    def test_a_configured_url_contributes_its_origin_not_its_path(self) -> None:
        """`auth_frontend_url` is a sign-in *page*, and on the deployment
        that failed it read `https://<host>/auth`. No browser ever sends
        that as an `Origin`, so as a candidate it was dead weight that
        looked like coverage."""
        # Asserted as the whole tuple rather than with `in`: exact, and it
        # also pins the order and the de-duplication, which a membership
        # check leaves free.
        assert self._allowed() == (
            "https://lemma.test",
            "https://www.lemma.test",
            "http://localhost:3000",
            "https://train.lemma.test",
            "https://api.lemma.test",
        )

    def test_widening_the_list_does_not_widen_it_to_everyone(self) -> None:
        allowed = self._allowed()

        for origin in (
            "https://evil.test",
            "https://lemma.test.evil.test",
            "https://wwwXlemma.test",
        ):
            assert origin_is_allowed(origin, allowed=allowed) is False, origin


class TestThePatternForPerTenantFrontends:
    def test_a_tenant_host_matches_the_configured_pattern(self) -> None:
        pattern = r"^https://[a-z0-9-]+\.workspaces\.lemma\.test$"

        assert (
            origin_is_allowed(
                "https://abc.workspaces.lemma.test", allowed=(), pattern=pattern
            )
            is True
        )

    def test_an_unanchored_pattern_is_anchored_anyway(self) -> None:
        """`re.fullmatch`, not `search`. A pattern written without anchors is
        the ordinary way a regex allowlist lets in
        `https://evil.test/?x=abc.workspaces.lemma.test`."""
        pattern = r"https://[a-z0-9-]+\.workspaces\.lemma\.test"

        assert (
            origin_is_allowed(
                "https://evil.test/?x=abc.workspaces.lemma.test",
                allowed=(),
                pattern=pattern,
            )
            is False
        )

    def test_a_pattern_that_will_not_compile_refuses(self) -> None:
        """The alternative is an allowlist that has silently stopped
        narrowing anything."""
        assert (
            origin_is_allowed("https://any.test", allowed=(), pattern="([unclosed")
            is False
        )


class TestTheRefusalSaysEnoughToDiagnose:
    """It said nothing at all, on the sound principle that an `Origin` is
    attacker-controlled and a newline in it forges a log line. The cost was
    six refusals in the record with no way to tell which origin or how many
    were configured -- the answer had to be rebuilt from deployment config.
    """

    def test_the_hint_carries_the_shape_and_the_size_of_the_list(self) -> None:
        hint = origin_refusal_hint(
            "https://www.lemma.test/x?y=1", allowed=("https://a.test", "https://b.test")
        )

        assert "lemma.test" in hint
        assert "2" in hint

    def test_a_malformed_origin_refuses_instead_of_raising(self) -> None:
        """`urlsplit` raises on a bad authority, and this runs *before*
        authentication -- so without the guard one header buys an
        unauthenticated caller an unhandled ASGI exception instead of a
        close code."""
        for bad in ("http://[", "https://[::1", "http://a]b"):
            assert origin_is_allowed(bad, allowed=("https://a.test",)) is False, bad
            assert origin_refusal_hint(bad, allowed=()), bad

    def test_a_configured_url_that_will_not_parse_is_skipped(self) -> None:
        """A settings typo must not stop the allowlist being built from the
        entries that are fine."""
        allowed = origins_from(["http://[", "https://good.test"])

        assert allowed == ("https://good.test",)

    def test_the_hint_never_carries_what_a_stranger_sent(self) -> None:
        hint = origin_refusal_hint(
            "https://evil.test/\n\rFAKE level=info event=allowed", allowed=()
        )

        assert "\n" not in hint and "\r" not in hint
        assert "FAKE" not in hint
        assert "?" not in hint and "/" not in hint.split("//", 1)[-1]


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


# ---------------------------------------------------------------------------
# Putting the display back
# ---------------------------------------------------------------------------


class _ResettableService:
    """A view service that records whether it was asked to reset."""

    def __init__(self, *, fails: bool = False) -> None:
        self.resets = 0
        self.fails = fails

    #: What the sandbox's own relay says is watching. `None` is an older
    #: image that cannot answer.
    watching: int | None = 0

    async def viewers(self, _user_id) -> int | None:
        return self.watching

    async def reset_display(self, _user_id) -> str:
        self.resets += 1
        if self.fails:
            raise RuntimeError("the relay went away")
        return "1440x960"

    async def close(self) -> None:
        return None


@pytest.fixture
def watching(monkeypatch):
    """The viewer bookkeeping, with the settle window collapsed.

    The service and the window are both parameters of `watch_ended`, so the
    double goes in through the call rather than being patched onto the
    module: patching the constructor a subject reaches for from inside it
    puts a double in front of half of what is under test.
    """
    from app.modules.workspace.api.controllers import browser_view_watchers as mod

    service = _ResettableService()
    watcher = uuid4()
    try:
        yield mod, watcher, service
    finally:
        mod._watchers.pop(watcher, None)


def _ended(mod, watcher, service) -> None:
    """A viewer leaving, with the collaborators injected."""
    mod.watch_ended(watcher, build_service=lambda: service, settle_seconds=0.01)


async def _settle() -> None:
    """Let the detached reset run."""
    await asyncio.sleep(0.05)


async def test_the_last_viewer_leaving_puts_the_display_back(watching) -> None:
    """Counted server-side, because a pane often never runs its cleanup.

    A closed tab, a killed renderer or a dropped network fires no unmount.
    The socket closing is the only signal that is always there, which is why
    this does not live in the React effect it would be tidier in.
    """
    mod, watcher, service = watching
    mod._watchers[watcher] = 1

    _ended(mod, watcher, service)
    await _settle()

    assert service.resets == 1
    assert watcher not in mod._watchers


async def test_a_second_viewer_leaving_does_not_resize_under_the_first(
    watching,
) -> None:
    """Two people can watch one display. The first to close must not take the
    other's picture back to the default shape underneath them."""
    mod, watcher, service = watching
    mod._watchers[watcher] = 2

    _ended(mod, watcher, service)
    await _settle()
    assert service.resets == 0, "somebody is still watching"
    assert mod._watchers[watcher] == 1

    _ended(mod, watcher, service)
    await _settle()
    assert service.resets == 1


async def test_somebody_reconnecting_keeps_their_shape(watching) -> None:
    """The reason the reset waits at all.

    A socket closing is not a person leaving: a dropped network, a reload,
    and the pane's own retry after `CLOSE_NO_BROWSER` each close one and open
    another a moment later. Resetting on the close resized the display under
    the handshake that followed -- which the browser e2e caught as a
    framebuffer that never painted, having agreed its dimensions just before
    they changed.
    """
    mod, watcher, service = watching
    mod._watchers[watcher] = 1

    _ended(mod, watcher, service)
    # Arrives while the reset is still settling, as a reconnect does.
    mod._watchers[watcher] = 1
    await _settle()

    assert service.resets == 0


async def test_a_reset_that_fails_does_not_fail_the_socket(watching) -> None:
    """Tidying up is best effort. The socket has already done its job, and a
    sandbox that went away between the last frame and the close is ordinary."""
    mod, watcher, _ = watching
    broken = _ResettableService(fails=True)
    mod._watchers[watcher] = 1

    _ended(mod, watcher, broken)
    await _settle()

    assert broken.resets == 1


async def test_a_viewer_on_another_worker_keeps_their_shape(watching) -> None:
    """The count that decides this lives in the sandbox, not in a process.

    `_watchers` is a module-level dict, so two people watching one sandbox
    through different API workers each see a count of one. The first to
    close saw zero locally and reset the display under the second. The relay
    is per-sandbox, so its count is the only one with a single answer.
    """
    mod, watcher, service = watching
    service.watching = 1
    mod._watchers[watcher] = 1

    _ended(mod, watcher, service)
    await _settle()

    assert service.resets == 0, "somebody is still attached to this sandbox"


async def test_an_image_that_cannot_count_falls_back_to_the_local_view(
    watching,
) -> None:
    """An older relay has no `viewers` field. Refusing to reset on that
    would leave every display stuck at the last pane's shape, which is the
    bug the reset exists for."""
    mod, watcher, service = watching
    service.watching = None
    mod._watchers[watcher] = 1

    _ended(mod, watcher, service)
    await _settle()

    assert service.resets == 1


# ---------------------------------------------------------------------------
# A fabric that refuses the relay's port
# ---------------------------------------------------------------------------


class _PortRefusedService(_FakeService):
    """The desktop guest saying it does not serve 4850.

    `reach_port` raises `ProviderRejected` for a port the fabric does not
    publish, and the relay client turns that into `BrowserRelayNotServed`.
    Before it had a type, it reached this handler as a `ProviderRejected`
    nothing caught, and an unhandled exception in a socket handler arrives at
    the pane as an ordinary drop -- which it retried for ever, each attempt a
    round trip on the guest's single control channel.
    """

    def __init__(self) -> None:
        from app.modules.workspace.services.browser_relay_client import (
            BrowserRelayNotServed,
        )

        super().__init__(
            fail=BrowserRelayNotServed("this sandbox does not serve the browser relay")
        )


def test_a_fabric_that_refuses_the_relays_port_is_a_terminal_close() -> None:
    service = _PortRefusedService()

    async def _signed_in(_websocket):
        return str(uuid4())

    client = _client(service)
    client.app.dependency_overrides[user_id_resolver] = lambda: _signed_in
    with client.websocket_connect(
        "/workspace/browser/view", headers={"Origin": "https://app.lemma.test"}
    ) as socket:
        refusal = socket.receive()

    assert refusal["type"] == "websocket.close"
    assert refusal["code"] == view.CLOSE_RELAY_ABSENT, (
        "a refused port must be a code the pane stops retrying on"
    )
    assert service.closed, "the service was left open on the way out"


async def test_a_refused_port_renders_a_state_rather_than_a_traceback() -> None:
    """`/workspace/browser/status` exists to render a panel.

    It answered every other "not right now" with a state and this one with a
    500, because the fabric's refusal had no type anything here caught — so the
    status route failed on exactly the installs where the pane did not work.
    "unavailable", not "asleep": waking a sandbox that does not publish the
    relay's port changes nothing about whether it publishes it.
    """
    from app.modules.workspace.services import browser_view_service as service_module
    from app.modules.workspace.services.browser_relay_client import (
        BrowserRelayNotServed,
    )

    class _RefusedRelay:
        """The sandbox, standing in for the one the fabric will not reach."""

        async def health(self, *, start: bool = False) -> str:
            raise BrowserRelayNotServed("this sandbox does not serve the browser relay")

    class _Refusing(service_module.BrowserViewService):
        async def _relay(self, user_id, *, start, deliver=True):
            return _RefusedRelay()

    found = await _Refusing().status(uuid4())

    assert found["state"] == "unavailable"


async def test_a_poll_for_the_current_page_writes_nothing_into_the_sandbox() -> None:
    """Reading where the browser is must not cost two writes into it.

    `_relay` delivers the relay token and the browser-proxy decision on every
    call, which is right for somebody arriving and wrong for a poll: the
    sign-in pane asks this every 1.5 seconds for as long as it is open. On
    Desktop each write is a round trip through the guest's one vsock control
    channel, so an idle pane took that channel four times every other second --
    the same channel every other sandbox operation on the machine waits for.
    """
    from app.modules.workspace.services import browser_view_service as service_module

    delivered: list[str] = []

    class _CountingRelay:
        async def deliver_token(self) -> None:
            delivered.append("token")

        async def deliver_browser_proxy(self, sandbox_id, kind) -> None:
            delivered.append("proxy")

        async def targets(self, *, domain=None):
            return [{"url": "https://example.test/account"}]

    class _Reading(service_module.BrowserViewService):
        async def _relay(self, user_id, *, start, deliver=True):
            relay = _CountingRelay()
            if deliver:
                await relay.deliver_token()
                await relay.deliver_browser_proxy(None, None)
            return relay

    service = _Reading()
    url = await service.current_page_url(uuid4(), origin="https://example.test")

    assert url == "https://example.test/account"
    assert delivered == [], f"a read wrote {delivered} into the sandbox"

    # And the flag is a choice rather than a removal: the default still writes,
    # which is what an arriving viewer depends on.
    await service._relay(uuid4(), start=True)
    assert delivered == ["token", "proxy"]
